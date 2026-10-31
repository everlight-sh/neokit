import Counter from "./Counter.jsx";

function Row({row}) {
  return <tr data-id={row.id}><td>{row.id}</td><td>{row.name}</td><td>{row.price}</td><td>{row.stock}</td><td>{row.note}</td></tr>;
}
export default function Catalog({value, interactive=false}) {
  return <main id="benchmark" data-n={value.n} data-seed={value.seed}><h1>Framework catalog</h1><p className="request" id="request-token">{value.token}</p><output className="stamp" id="render-stamp">{value.stamp}</output>{interactive && <Counter/>}<table><thead><tr><th>ID</th><th>Name</th><th>Price</th><th>Stock</th><th>Note</th></tr></thead><tbody id="catalog-rows">{value.rows.map(row => <Row key={row.id} row={row}/>)}</tbody><tfoot><tr><td colSpan="5" id="checksum">{value.checksum}</td></tr></tfoot></table></main>;
}
