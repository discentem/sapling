/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This software may be used and distributed according to the terms of the
 * GNU General Public License version 2.
 */

use std::str::FromStr;

use clidispatch::ReqCtx;
use configloader::convert::ByteCount;
use configmodel::ConfigExt;
use revisionstore::CorruptionPolicy;
use revisionstore::DataPackStore;
use revisionstore::ExtStoredPolicy;
use revisionstore::HgIdDataStore;
use revisionstore::IndexedLogHgIdDataStore;
use revisionstore::IndexedLogHgIdDataStoreConfig;
use revisionstore::StoreKey;
use revisionstore::StoreResult;
use revisionstore::StoreType;
use revisionstore::UnionHgIdDataStore;
use types::HgId;
use types::Key;
use types::RepoPathBuf;

use super::define_flags;
use super::Repo;
use super::Result;

define_flags! {
    pub struct DebugstoreOpts {
        /// print blob contents
        content: bool,

        #[arg]
        path: String,

        #[arg]
        hgid: String,
    }
}

pub fn run(ctx: ReqCtx<DebugstoreOpts>, repo: &mut Repo) -> Result<u8> {
    let path = RepoPathBuf::from_string(ctx.opts.path)?;
    let hgid = HgId::from_str(&ctx.opts.hgid)?;
    let config = repo.config();

    let packs_path = revisionstore::util::get_cache_path(config, &Some("packs"))?;
    let packstore = Box::new(DataPackStore::new(
        packs_path,
        CorruptionPolicy::IGNORE,
        None,
        ExtStoredPolicy::Use,
    ));

    let datastore_path = revisionstore::util::get_cache_path(config, &Some("indexedlogdatastore"))?;

    let max_log_count = config.get_opt::<u8>("indexedlog", "data.max-log-count")?;
    let max_bytes_per_log = config.get_opt::<ByteCount>("indexedlog", "data.max-bytes-per-log")?;
    let max_bytes = config.get_opt::<ByteCount>("remotefilelog", "cachelimit")?;
    let indexedlog_config = IndexedLogHgIdDataStoreConfig {
        max_log_count,
        max_bytes_per_log,
        max_bytes,
    };

    let indexedstore = Box::new(
        IndexedLogHgIdDataStore::new(
            datastore_path,
            ExtStoredPolicy::Use,
            &indexedlog_config,
            StoreType::Local,
        )
        .unwrap(),
    );
    let mut unionstore: UnionHgIdDataStore<Box<dyn HgIdDataStore>> = UnionHgIdDataStore::new();
    unionstore.add(packstore);
    unionstore.add(indexedstore);
    let k = Key::new(path, hgid);
    if let StoreResult::Found(content) = unionstore.get(StoreKey::hgid(k))? {
        ctx.core.io.write(content)?;
    }
    Ok(0)
}

pub fn aliases() -> &'static str {
    "debugstore"
}

pub fn doc() -> &'static str {
    "print information about blobstore"
}

pub fn synopsis() -> Option<&'static str> {
    None
}
