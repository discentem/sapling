// @generated by autocargo

use std::env;
use std::fs;
use std::path::Path;
use thrift_compiler::Config;
use thrift_compiler::GenContext;
const CRATEMAP: &str = "\
thrift_convert_test crate //eden/mononoke/common/thrift_convert/tests/if:thrift-convert-test-rust
";
#[rustfmt::skip]
fn main() {
    println!("cargo:rerun-if-changed=thrift_build.rs");
    let out_dir = env::var_os("OUT_DIR").expect("OUT_DIR env not provided");
    let cratemap_path = Path::new(&out_dir).join("cratemap");
    fs::write(cratemap_path, CRATEMAP).expect("Failed to write cratemap");
    let mut conf = Config::from_env(GenContext::Types)
        .expect("Failed to instantiate thrift_compiler::Config");
    conf.base_path("../../../../../..");
    conf.types_crate("thrift-convert-test__types");
    conf.clients_crate("thrift-convert-test__clients");
    let srcs: &[&str] = &["thrift_convert_test.thrift"];
    conf.run(srcs).expect("Failed while running thrift compilation");
}
