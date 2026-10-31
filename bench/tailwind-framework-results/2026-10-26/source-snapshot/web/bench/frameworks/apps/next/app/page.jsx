import Catalog from "./../components/Catalog.jsx";
import { data } from "./../../../shared/model.js";
import { connection } from "next/server";
export default async function Page({searchParams}) { await connection(); const query=await searchParams; return <Catalog value={data(query,3,Date.now())} interactive={false} metrics={query.metrics === "1"}/>; }
