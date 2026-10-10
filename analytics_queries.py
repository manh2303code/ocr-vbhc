"""Named, latest-version-only queries for the analytics UI."""


def fetch_latest_documents(
    conn, *, date_from=None, date_to=None, number=None,
    keyword=None, doc_type=None, org=None,
):
    query = """
        SELECT d.id, d.file_path, d.file_name, d.created_at,
               dv.so_ki_hieu, dv.ngay_bh, dv.loai_vb,
               dv.cqbh_tren, dv.nd_chinh, dv.do_khan
        FROM documents d
        LEFT JOIN document_versions dv ON dv.id = (
            SELECT latest.id FROM document_versions latest
            WHERE latest.document_id = d.id
            ORDER BY latest.version_number DESC, latest.id DESC LIMIT 1
        )
        WHERE 1=1
    """
    params = []
    if date_from:
        query += " AND d.created_at >= ?"
        params.append(date_from)
    if date_to:
        query += " AND d.created_at <= ?"
        params.append(f"{date_to} 23:59:59")
    if number:
        query += " AND dv.so_ki_hieu LIKE ?"
        params.append(f"%{number}%")
    if keyword:
        query += " AND (dv.nd_chinh LIKE ? OR dv.so_ki_hieu LIKE ? OR dv.cqbh_tren LIKE ?)"
        params.extend([f"%{keyword}%"] * 3)
    if doc_type:
        query += " AND dv.loai_vb LIKE ?"
        params.append(f"%{doc_type}%")
    if org:
        query += " AND dv.cqbh_tren LIKE ?"
        params.append(f"%{org}%")
    query += " ORDER BY d.created_at DESC"
    return conn.execute(query, params).fetchall()

