import Counter from "./Counter.jsx";

function Row({row}) {
  return <tr data-id={row.id}><td>{row.id}</td><td>{row.name}</td><td>{row.price}</td><td>{row.stock}</td><td>{row.note}</td></tr>;
}
export default function Catalog({value, interactive=false, metrics=false}) {
  return <main className="mx-auto my-12 max-w-[1100px] px-6" id="benchmark" data-n={value.n} data-seed={value.seed}><h1 className="mb-5 font-serif text-5xl tracking-[-2px]">Framework catalog</h1><p className="request" id="request-token">{value.token}</p><output className="stamp" id="render-stamp">{value.stamp}</output>{interactive && <Counter/>}<table className="w-full border-collapse bg-white"><thead><tr><th>ID</th><th>Name</th><th>Price</th><th>Stock</th><th>Note</th></tr></thead><tbody id="catalog-rows">{value.rows.map(row => <Row key={row.id} row={row}/>)}</tbody><tfoot><tr><td colSpan="5" id="checksum">{value.checksum}</td></tr></tfoot></table>{metrics && <script defer src="/browser-metrics.js"/>}</main>;
}
