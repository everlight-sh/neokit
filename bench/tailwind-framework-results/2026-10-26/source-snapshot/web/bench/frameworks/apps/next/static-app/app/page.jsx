import Catalog from "./../../components/Catalog.jsx";
import { data } from "./../../../../shared/model.js";
export default function Page() { return <Catalog value={data({},3,0)} interactive={false}/>; }
