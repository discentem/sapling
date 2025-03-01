/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This software may be used and distributed according to the terms of the
 * GNU General Public License version 2.
 */

mod mem_writes_changesets;
use std::collections::HashMap;
use std::path::Path;
use std::path::PathBuf;
use std::sync::Arc;

use anyhow::Context;
use anyhow::Error;
use blobrepo::BlobRepo;
use blobrepo_override::DangerousOverride;
use blobstore::Blobstore;
use blobstore::Loadable;
use bonsai_hg_mapping::ArcBonsaiHgMapping;
use bonsai_hg_mapping::MemWritesBonsaiHgMapping;
use cacheblob::dummy::DummyLease;
use cacheblob::LeaseOps;
use cacheblob::MemWritesBlobstore;
use changesets::ArcChangesets;
use clap::Parser;
use clap::Subcommand;
use context::CoreContext;
use fbinit::FacebookInit;
use futures::future;
use import_tools::create_changeset_for_annotated_tag;
use import_tools::import_tree_as_single_bonsai_changeset;
use import_tools::upload_git_tag;
use import_tools::GitimportPreferences;
use import_tools::GitimportTarget;
use linked_hash_map::LinkedHashMap;
use mercurial_derivation::get_manifest_from_bonsai;
use mercurial_derivation::DeriveHgChangeset;
use mononoke_api::BookmarkCategory;
use mononoke_api::BookmarkFreshness;
use mononoke_api::BookmarkKey;
use mononoke_app::args::RepoArgs;
use mononoke_app::fb303::AliveService;
use mononoke_app::fb303::Fb303AppExtension;
use mononoke_app::MononokeApp;
use mononoke_app::MononokeAppBuilder;
use mononoke_types::ChangesetId;
use repo_authorization::AuthorizationContext;
use repo_blobstore::RepoBlobstoreArc;
use repo_blobstore::RepoBlobstoreRef;
use repo_identity::RepoIdentityRef;
use slog::info;

use crate::mem_writes_changesets::MemWritesChangesets;

// Refactor this a bit. Use a thread pool for git operations. Pass that wherever we use store repo.
// Transform the walk into a stream of commit + file changes.

async fn derive_hg(
    ctx: &CoreContext,
    repo: &BlobRepo,
    import_map: impl Iterator<Item = (&git_hash::ObjectId, &ChangesetId)>,
) -> Result<(), Error> {
    let mut hg_manifests = HashMap::new();

    for (id, bcs_id) in import_map {
        let bcs = bcs_id.load(ctx, repo.repo_blobstore()).await?;
        let parent_manifests = future::try_join_all(bcs.parents().map({
            let hg_manifests = &hg_manifests;
            move |p| async move {
                let manifest = if let Some(manifest) = hg_manifests.get(&p) {
                    *manifest
                } else {
                    repo.derive_hg_changeset(ctx, p)
                        .await?
                        .load(ctx, repo.repo_blobstore())
                        .await?
                        .manifestid()
                };
                Result::<_, Error>::Ok(manifest)
            }
        }))
        .await?;

        let manifest = get_manifest_from_bonsai(
            ctx.clone(),
            repo.repo_blobstore_arc(),
            bcs.clone(),
            parent_manifests,
        )
        .await?;

        hg_manifests.insert(*bcs_id, manifest);

        info!(ctx.logger(), "Hg: {:?}: {:?}", id, manifest);
    }

    Ok(())
}

/// Mononoke Git Importer
#[derive(Parser)]
struct GitimportArgs {
    #[clap(long)]
    derive_hg: bool,
    /// This is used to suppress the printing of the potentially really long git Reference -> BonzaiID mapping.
    #[clap(long)]
    suppress_ref_mapping: bool,
    /// **Dangerous** Generate bookmarks for all git refs (tags and branches)
    /// Make sure not to use this on a mononoke repo in production or you will overwhelm any
    /// service doing backfilling on public changesets!
    /// Use at your own risk!
    #[clap(long)]
    generate_bookmarks: bool,
    /// When set, the gitimport tool would bypass the read-only check while creating and moving bookmarks.
    #[clap(long)]
    bypass_readonly: bool,
    /// Set the path to the git binary - preset to git.real
    #[clap(long)]
    git_command_path: Option<String>,
    /// Path to a git repository to import
    git_repository_path: String,
    /// Reupload git commits, even if they already exist in Mononoke
    #[clap(long)]
    reupload_commits: bool,
    #[clap(subcommand)]
    subcommand: GitimportSubcommand,
    #[clap(flatten)]
    repo_args: RepoArgs,
}

#[derive(Subcommand)]
enum GitimportSubcommand {
    /// Import all of the commits in this repo
    FullRepo,
    /// Import all commits between <GIT_FROM> and <GIT_TO>
    GitRange {
        git_from: String,
        git_to: String,
    },
    /// Import <GIT_COMMIT> and all its history that's not yet been imported.
    /// Makes a pass over the repo on construction to find missing history
    MissingForCommit {
        git_commit: String,
    },
    ImportTreeAsSingleBonsaiChangeset {
        git_commit: String,
    },
}

#[fbinit::main]
fn main(fb: FacebookInit) -> Result<(), Error> {
    let app = MononokeAppBuilder::new(fb)
        .with_app_extension(Fb303AppExtension {})
        .build::<GitimportArgs>()?;

    app.run_with_monitoring_and_logging(async_main, "gitimport", AliveService)
}

async fn async_main(app: MononokeApp) -> Result<(), Error> {
    let logger = app.logger();
    let ctx = CoreContext::new_with_logger(app.fb, logger.clone());
    let args: GitimportArgs = app.args()?;
    let mut prefs = GitimportPreferences::default();

    // if we are readonly, then we'll set up some overrides to still be able to do meaningful
    // things below.
    let dry_run = app.readonly_storage().0;
    prefs.dry_run = dry_run;

    if let Some(path) = args.git_command_path {
        prefs.git_command_path = PathBuf::from(path);
    }

    let path = Path::new(&args.git_repository_path);

    let reupload = if args.reupload_commits {
        import_direct::ReuploadCommits::Always
    } else {
        import_direct::ReuploadCommits::Never
    };

    let repo: BlobRepo = app.open_repo(&args.repo_args).await?;
    info!(
        logger,
        "using repo \"{}\" repoid {:?}",
        repo.repo_identity().name(),
        repo.repo_identity().id(),
    );

    let repo = if dry_run {
        repo.dangerous_override(|blobstore| -> Arc<dyn Blobstore> {
            Arc::new(MemWritesBlobstore::new(blobstore))
        })
        .dangerous_override(|changesets| -> ArcChangesets {
            Arc::new(MemWritesChangesets::new(changesets))
        })
        .dangerous_override(|bonsai_hg_mapping| -> ArcBonsaiHgMapping {
            Arc::new(MemWritesBonsaiHgMapping::new(bonsai_hg_mapping))
        })
        .dangerous_override(|_| Arc::new(DummyLease {}) as Arc<dyn LeaseOps>)
    } else {
        repo
    };

    let uploader = import_direct::DirectUploader::new(repo.clone(), reupload);

    let target = match args.subcommand {
        GitimportSubcommand::FullRepo {} => GitimportTarget::full(),
        GitimportSubcommand::GitRange { git_from, git_to } => {
            let from = git_from.parse()?;
            let to = git_to.parse()?;
            import_direct::range(from, to, &ctx, &repo).await?
        }
        GitimportSubcommand::MissingForCommit { git_commit } => {
            let commit = git_commit.parse()?;
            import_direct::missing_for_commit(commit, &ctx, &repo, &prefs.git_command_path, path)
                .await?
        }
        GitimportSubcommand::ImportTreeAsSingleBonsaiChangeset { git_commit } => {
            let commit = git_commit.parse()?;
            let bcs_id =
                import_tree_as_single_bonsai_changeset(&ctx, path, uploader, commit, &prefs)
                    .await?;
            info!(ctx.logger(), "imported as {}", bcs_id);
            if args.derive_hg {
                derive_hg(&ctx, &repo, [(&commit, &bcs_id)].into_iter()).await?;
            }
            return Ok(());
        }
    };

    let gitimport_result: LinkedHashMap<_, _> =
        import_tools::gitimport(&ctx, path, &uploader, &target, &prefs)
            .await
            .context("gitimport failed")?;
    if args.derive_hg {
        derive_hg(&ctx, &repo, gitimport_result.iter())
            .await
            .context("derive_hg failed")?;
    }

    if !args.suppress_ref_mapping || args.generate_bookmarks {
        let refs = import_tools::read_git_refs(path, &prefs)
            .await
            .context("read_git_refs failed")?;
        let mapping = refs
            .iter()
            .map(|(git_ref, commit)| {
                (
                    git_ref.maybe_tag_id,
                    String::from_utf8_lossy(&git_ref.name),
                    gitimport_result.get(commit),
                )
            })
            .collect::<Vec<_>>();
        if !args.suppress_ref_mapping {
            for (_, name, changeset) in &mapping {
                info!(ctx.logger(), "Ref: {:?}: {:?}", name, changeset);
            }
        }
        if args.generate_bookmarks {
            let authz = AuthorizationContext::new_bypass_access_control();
            let repo_context = app
                .open_managed_repo_arg(&args.repo_args)
                .await
                .context("failed to create mononoke app")?
                .make_mononoke_api()?
                .repo_by_id(ctx.clone(), repo.repo_identity().id())
                .await
                .with_context(|| format!("failed to access repo: {}", repo.repo_identity().id()))?
                .expect("repo exists")
                .with_authorization_context(authz)
                .build()
                .await
                .context("failed to build RepoContext")?;
            for (maybe_tag_id, name, changeset) in
                mapping
                    .iter()
                    .filter_map(|(maybe_tag_id, name, changeset)| {
                        changeset.map(|cs| (maybe_tag_id, name, cs))
                    })
            {
                let final_changeset = changeset.clone();
                let mut name = name
                    .strip_prefix("refs/")
                    .context("Ref does not start with refs/")?
                    .to_string();
                if name.starts_with("remotes/origin/") {
                    name = name.replacen("remotes/origin/", "heads/", 1);
                };
                if name.as_str() == "heads/HEAD" {
                    // Skip the HEAD revision: it shouldn't be imported as a bookmark in mononoke
                    continue;
                }
                if let Some(tag_id) = maybe_tag_id {
                    // The ref getting imported is a tag, so store the raw git Tag object.
                    upload_git_tag(&ctx, &uploader, path, &prefs, tag_id).await?;
                    // Create the changeset corresponding to the commit pointed to by the tag.
                    create_changeset_for_annotated_tag(
                        &ctx, &uploader, path, &prefs, tag_id, changeset,
                    )
                    .await?;
                }
                // Set the appropriate category for branch and tag bookmarks
                let bookmark_key = if maybe_tag_id.is_some() {
                    BookmarkKey::with_name_and_category(name.parse()?, BookmarkCategory::Tag)
                } else {
                    BookmarkKey::new(&name)?
                };

                let pushvars = if args.bypass_readonly {
                    Some(HashMap::from_iter([(
                        "BYPASS_READONLY".to_string(),
                        bytes::Bytes::from("true"),
                    )]))
                } else {
                    None
                };
                let old_changeset = repo_context
                    .resolve_bookmark(&bookmark_key, BookmarkFreshness::MostRecent)
                    .await
                    .with_context(|| format!("failed to resolve bookmark {name}"))?
                    .map(|context| context.id());
                match old_changeset {
                    // The bookmark already exists. Instead of creating it, we need to move it.
                    Some(old_changeset) => {
                        if old_changeset != final_changeset {
                            let allow_non_fast_forward = true;
                            repo_context
                                .move_bookmark(
                                    &bookmark_key,
                                    final_changeset,
                                    Some(old_changeset),
                                    allow_non_fast_forward,
                                    pushvars.as_ref(),
                                )
                                .await
                                .with_context(|| format!("failed to move bookmark {name} from {old_changeset:?} to {final_changeset:?}"))?;
                            info!(
                                ctx.logger(),
                                "Bookmark: \"{name}\": {final_changeset:?} (moved from {old_changeset:?})"
                            );
                        } else {
                            info!(
                                ctx.logger(),
                                "Bookmark: \"{name}\": {final_changeset:?} (already up-to-date)"
                            );
                        }
                    }
                    // The bookmark doesn't yet exist. Create it.
                    None => {
                        repo_context
                            .create_bookmark(&bookmark_key, final_changeset, pushvars.as_ref())
                            .await
                            .with_context(|| {
                                format!("failed to create bookmark {name} during gitimport")
                            })?;
                        info!(
                            ctx.logger(),
                            "Bookmark: \"{name}\": {final_changeset:?} (created)"
                        )
                    }
                }
            }
        };
    }
    Ok(())
}
