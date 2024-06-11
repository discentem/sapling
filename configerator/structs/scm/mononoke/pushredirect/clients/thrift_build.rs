// @generated by autocargo

use std::env;
use std::fs;
use std::path::Path;
use thrift_compiler::Config;
use thrift_compiler::GenContext;
const CRATEMAP: &str = "\
enable crate //configerator/structs/scm/mononoke/pushredirect:pushredirect_enable-rust
rust rust //thrift/annotation:rust-rust
";
#[rustfmt::skip]
fn main() {
    println!("cargo:rerun-if-changed=thrift_build.rs");
    let out_dir = env::var_os("OUT_DIR").expect("OUT_DIR env not provided");
    let cratemap_path = Path::new(&out_dir).join("cratemap");
    fs::write(cratemap_path, CRATEMAP).expect("Failed to write cratemap");
    let mut conf = Config::from_env(GenContext::Clients)
        .expect("Failed to instantiate thrift_compiler::Config");
    conf.base_path("../../../../../..");
    conf.types_crate("pushredirect_enable__types");
    conf.clients_crate("pushredirect_enable__clients");
    conf.options("serde");
    let srcs: &[&str] = &["../enable.thrift"];
    conf.run(srcs).expect("Failed while running thrift compilation");
}
