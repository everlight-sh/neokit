// Catalog SSR in Rust: hyper 1 + tokio, same markup, escaping and query rules as the framework app.
use std::convert::Infallible;
use std::fmt::Write;
use std::net::SocketAddr;
use std::time::{SystemTime, UNIX_EPOCH};

use http_body_util::Full;
use hyper::body::{Bytes, Incoming};
use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper::{Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use tokio::net::TcpListener;

const WORDS: [&str; 8] = ["Widget", "Gadget", "Gizmo", "Doohickey", "Sprocket", "Flange", "Bracket", "Grommet"];
const ADJECTIVES: [&str; 8] = ["Deluxe", "Basic", "Pro", "Mini", "Ultra", "Classic", "Eco", "Max"];
const SPECIAL: &str = "Ben & Jerry's \"Special\" <limited> café";

fn escape_into(out: &mut String, s: &str) {
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#x27;"),
            _ => out.push(c),
        }
    }
}

fn decode(v: &str) -> String {
    let b = v.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    let hex = |c: u8| (c as char).to_digit(16).map(|d| d as u8);
    while i < b.len() {
        match b[i] {
            b'+' => out.push(b' '),
            b'%' if i + 2 < b.len() => match (hex(b[i + 1]), hex(b[i + 2])) {
                (Some(h), Some(l)) => { out.push(h * 16 + l); i += 2; }
                _ => out.push(b'%'),
            },
            c => out.push(c),
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn query<'a>(q: &'a str, key: &str) -> Option<String> {
    q.split('&').filter_map(|kv| kv.split_once('=').or(Some((kv, "")))).find(|(k, _)| *k == key).map(|(_, v)| decode(v))
}

fn number(q: &str, key: &str, fallback: i64, min: i64, max: i64) -> i64 {
    match query(q, key) {
        Some(v) if (1..=4).contains(&v.len()) && v.bytes().all(|c| c.is_ascii_digit()) => {
            let n: i64 = v.parse().unwrap();
            if n >= min && n <= max { n } else { fallback }
        }
        _ => fallback,
    }
}

fn render(n: i64, seed: i64, token: &str, interactive: bool, css: &str, js: &str) -> String {
    let mut s = String::with_capacity(600 + 160 * n as usize);
    s.push_str("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>Framework catalog</title><link rel=\"stylesheet\" href=\"");
    s.push_str(css);
    let _ = write!(s, "\"></head><body><main id=\"benchmark\" data-n=\"{n}\" data-seed=\"{seed}\"><h1>Framework catalog</h1><p class=\"request\" id=\"request-token\">");
    escape_into(&mut s, token);
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis();
    let _ = write!(s, "</p><output class=\"stamp\" id=\"render-stamp\">{stamp}</output>");
    if interactive {
        s.push_str("<section class=\"counter\" data-web-widget=\"counter\"><output data-count aria-label=\"Counter value\" aria-live=\"polite\">0</output><button type=\"button\" data-increment>Add one +</button><button type=\"button\" data-reset>Reset</button></section>");
    }
    s.push_str("<table><thead><tr><th>ID</th><th>Name</th><th>Price</th><th>Stock</th><th>Note</th></tr></thead><tbody id=\"catalog-rows\">");
    let mut checksum = 0;
    for i in 0..n {
        let price = (i * 37 + seed * 13) % 1000;
        let stock = (i * 13 + seed) % 50;
        checksum += price + stock;
        let _ = write!(s, "<tr data-id=\"{i}\"><td>{i}</td><td>");
        s.push_str(WORDS[((i + seed) % 8) as usize]);
        s.push(' ');
        s.push_str(ADJECTIVES[((i * 7 + seed) % 8) as usize]);
        let _ = write!(s, "</td><td>{price}.99</td><td>{stock}</td><td>");
        escape_into(&mut s, if i % 5 == 0 { SPECIAL } else { "-" });
        s.push_str("</td></tr>");
    }
    let _ = write!(s, "</tbody><tfoot><tr><td colspan=\"5\" id=\"checksum\">{checksum}</td></tr></tfoot></table></main>\n");
    if interactive {
        let _ = write!(s, "<script type=\"module\" src=\"{js}\"></script>");
    }
    s.push_str("</body></html>\n");
    s
}

async fn handle(req: Request<Incoming>, css: &'static str, js: &'static str) -> Result<Response<Full<Bytes>>, Infallible> {
    let route = match req.uri().path() {
        "/" => Some((3, false)),
        "/large" => Some((1000, false)),
        "/interactive" => Some((50, true)),
        _ => None,
    };
    let Some((default_n, interactive)) = route else {
        return Ok(Response::builder().status(StatusCode::NOT_FOUND).body(Full::new(Bytes::from_static(b"Not Found"))).unwrap());
    };
    let q = req.uri().query().unwrap_or("");
    let n = number(q, "n", default_n, 1, 1000);
    let seed = number(q, "seed", 17, 0, 999);
    let token = query(q, "token").filter(|t| t.len() <= 128).unwrap_or_else(|| "fixture".into());
    let body = render(n, seed, &token, interactive, css, js);
    Ok(Response::builder().header("Content-Type", "text/html; charset=utf-8").body(Full::new(Bytes::from(body))).unwrap())
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let port: u16 = std::env::args().nth(1).expect("port").parse()?;
    let css: &'static str = Box::leak(std::env::var("CSS_HREF").unwrap_or_else(|_| "/assets/site.css".into()).into_boxed_str());
    let js: &'static str = Box::leak(std::env::var("JS_HREF").unwrap_or_else(|_| "/assets/client.js".into()).into_boxed_str());
    let listener = TcpListener::bind(SocketAddr::from(([0, 0, 0, 0], port))).await?;
    loop {
        let (stream, _) = listener.accept().await?;
        let _ = stream.set_nodelay(true);
        tokio::spawn(async move {
            let _ = http1::Builder::new().serve_connection(TokioIo::new(stream), service_fn(move |r| handle(r, css, js))).await;
        });
    }
}
