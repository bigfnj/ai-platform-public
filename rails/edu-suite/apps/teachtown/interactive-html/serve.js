import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.dirname(fileURLToPath(import.meta.url));
const types = { ".html":"text/html; charset=utf-8", ".css":"text/css", ".js":"text/javascript", ".pdf":"application/pdf", ".mjs":"text/javascript" };
http.createServer((req,res)=>{
  const requested = decodeURIComponent((req.url || "/").split("?")[0]);
  const relative = requested === "/" ? "index.html" : requested.replace(/^\/+/, "");
  const file = path.resolve(root, relative);
  if (!file.startsWith(root)) { res.writeHead(403).end(); return; }
  fs.readFile(file,(error,data)=>{
    if(error){res.writeHead(404).end("Not found");return;}
    res.writeHead(200,{"Content-Type":types[path.extname(file).toLowerCase()]||"application/octet-stream"});res.end(data);
  });
}).listen(8765,"127.0.0.1",()=>console.log("TeachTown: http://127.0.0.1:8765"));
