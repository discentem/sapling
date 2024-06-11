// @generated by autocargo

use std::env;
use std::fs;
use std::path::Path;
use thrift_compiler::Config;
use thrift_compiler::GenContext;
const CRATEMAP: &str = "\
eden crate //eden/fs/service:thrift-rust
eden_config config_thrift //eden/fs/config:config_thrift-rust
fb303_core fb303_core //fb303/thrift:fb303_core-rust
thrift thrift //thrift/annotation:thrift-rust
";
#[rustfmt::skip]
fn main() {
    println!("cargo:rerun-if-changed=thrift_build.rs");
    let out_dir = env::var_os("OUT_DIR").expect("OUT_DIR env not provided");
    let cratemap_path = Path::new(&out_dir).join("cratemap");
    fs::write(cratemap_path, CRATEMAP).expect("Failed to write cratemap");
    let mut conf = Config::from_env(GenContext::Services)
        .expect("Failed to instantiate thrift_compiler::Config");
    conf.base_path("../../../..");
    conf.types_crate("thrift__types");
    conf.clients_crate("thrift__clients");
    conf.options("deprecated_default_enum_min_i32");
    let srcs: &[&str] = &["../eden.thrift"];
    conf.run(srcs).expect("Failed while running thrift compilation");
}
