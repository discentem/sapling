// @generated by autocargo

use std::env;
use std::fs;
use std::path::Path;
use thrift_compiler::Config;
use thrift_compiler::GenContext;
const CRATEMAP: &str = "\
lfs_server crate //configerator/structs/scm/mononoke/lfs_server:lfs_server_config-rust
ratelimits rate_limiting_config //configerator/structs/scm/mononoke/ratelimiting:rate_limiting_config-rust
rust rust //thrift/annotation:rust-rust
";
#[rustfmt::skip]
fn main() {
    println!("cargo:rerun-if-changed=thrift_build.rs");
    let out_dir = env::var_os("OUT_DIR").expect("OUT_DIR env not provided");
    let cratemap_path = Path::new(&out_dir).join("cratemap");
    fs::write(cratemap_path, CRATEMAP).expect("Failed to write cratemap");
    let mut conf = Config::from_env(GenContext::Mocks)
        .expect("Failed to instantiate thrift_compiler::Config");
    conf.base_path("../../../../../..");
    conf.types_crate("lfs_server_config__types");
    conf.clients_crate("lfs_server_config__clients");
    conf.options("serde");
    let srcs: &[&str] = &["../lfs_server.thrift"];
    conf.run(srcs).expect("Failed while running thrift compilation");
}
