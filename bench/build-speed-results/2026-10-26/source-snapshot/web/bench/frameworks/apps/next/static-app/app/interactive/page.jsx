import Catalog from "./../../../components/Catalog.jsx";
import { data } from "./../../../../../shared/model.js";
export default function Page() { return <Catalog value={data({},50,0)} interactive={true}/>; }
