export interface RelatedDoc {
  doc_id?: string
  title?: string
  relation_type?: string | null
}

/** Related-document label: omit the parenthetical when there is no relation type. */
export function relatedDocLabel(doc: RelatedDoc): string {
  const title = doc.title ?? ''
  const relation = (doc.relation_type ?? '').trim()
  return relation ? `${title} (${relation})` : title
}
