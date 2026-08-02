/** 查看指定文档的所有 chunk 全文 */
import pg from "pg";

const keyword = process.argv[2] || "考勤";
const pool = new pg.Pool({ host: "127.0.0.1", port: 5433, user: "pi", password: "pi_knowledge", database: "knowledge" });

const r = await pool.query(
  `SELECT c.seq, c.heading, c.content
   FROM chunks c JOIN documents d ON d.id = c.doc_id
   WHERE d.source_path LIKE $1
   ORDER BY c.seq`,
  [`%${keyword}%`],
);

console.log(`匹配文档含 "${keyword}" 的 chunks: ${r.rows.length} 个\n`);
for (const row of r.rows) {
  console.log(`========== chunk#${row.seq} (${row.content.length}字) ==========`);
  if (row.heading) console.log(`heading: ${row.heading}`);
  console.log(row.content);
  console.log();
}
await pool.end();
