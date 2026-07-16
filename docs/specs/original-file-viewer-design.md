# 文档原始文件查看功能设计

## 概述

在文档详情页新增"查看原始文件"功能，用户点击后可直接预览或下载上传时的原始文件。根据文件类型采用不同展示策略：图片内联弹窗展示、PDF 浏览器内联预览、其他类型触发下载。

## 背景

### 当前问题

1. **原始文件不可见**：文档详情页仅展示提取后的 `raw_text`（纯文本）和 LLM 生成的 `overview`（摘要），用户无法查看原始文件内容
2. **图片内容依赖 VLM 文本描述**：图片经 VLM 提取后仅以 markdown 文本呈现，用户无法看到原始图片
3. **PDF/DOCX/PPTX 缺乏原始格式展示**：这些文件类型提取后丢失了排版、图表等视觉信息

### 目标

1. 文档详情页提供"查看原始文件"按钮
2. 图片：弹窗内联展示原始图片
3. PDF：浏览器新标签页内联预览（利用浏览器内置 PDF 阅读器）
4. DOCX/PPTX/其他：触发文件下载
5. 正确处理非 ASCII 文件名（中文标题）

## 设计决策

| 维度 | 决策 | 理由 |
|------|------|------|
| 文件服务方式 | FastAPI `FileResponse` 直接返回本地文件 | 文件存储在 `uploads/` 目录，无需额外存储服务 |
| Content-Disposition 策略 | 图片/PDF 用 `inline`，其他用 `attachment` | 图片和 PDF 浏览器可内联展示，DOCX/PPTX 无浏览器原生支持 |
| 图片展示方式 | 前端弹窗（Modal）overlay | 不离开当前页面，点击遮罩关闭 |
| PDF 展示方式 | `window.open` 新标签页 | 浏览器内置 PDF 阅读器，无需额外组件 |
| 非 ASCII 文件名 | RFC 5987 编码（`filename*=UTF-8''...`） | HTTP 头仅支持 latin-1，需 URL 编码中文文件名 |
| 文件路径来源 | `Document.file_path` 字段 | 上传时已保存原始文件路径 |

## 架构变更

### 数据流

```
用户点击"查看原始文件"
    │
    ├── file_type === 'image'
    │       │
    │       ▼
    │   前端弹窗 <img src="/api/documents/{id}/file">
    │
    └── 其他类型
            │
            ▼
        window.open("/api/documents/{id}/file", '_blank')
            │
            ▼
    ┌──────────────────────────────────────────┐
    │  Backend: GET /documents/{doc_id}/file   │
    │                                          │
    │  1. 查询 Document.file_path             │
    │  2. 推断 Content-Type (mimetypes)        │
    │  3. 设置 Content-Disposition:            │
    │     - inline (图片/PDF)                  │
    │     - attachment (DOCX/PPTX/...)         │
    │  4. FileResponse 返回文件流               │
    └──────────────────────────────────────────┘
```

### 文件类型展示策略

```
┌─────────────┬────────────────┬──────────────────┬────────────────────┐
│  file_type  │ Content-Type   │ Disposition      │ 前端行为            │
├─────────────┼────────────────┼──────────────────┼────────────────────┤
│  image      │ image/png 等   │ inline           │ 弹窗 <img> 展示    │
│  pdf        │ application/pdf│ inline           │ 新标签页浏览器预览  │
│  docx       │ application/   │ attachment       │ 新标签页触发下载    │
│             │ vnd.openxml... │                  │                    │
│  pptx       │ application/   │ attachment       │ 新标签页触发下载    │
│             │ vnd.openxml... │                  │                    │
│  markdown   │ text/markdown  │ attachment       │ 新标签页触发下载    │
└─────────────┴────────────────┴──────────────────┴────────────────────┘
```

## 后端 API

### 新增 `GET /documents/{doc_id}/file`

返回文档的原始文件，支持浏览器内联展示或下载。

**路径参数:**

| 参数 | 类型 | 说明 |
|------|------|------|
| `doc_id` | UUID | 文档 ID |

**响应:**

- **200 OK**: `FileResponse` — 文件流，带有正确的 `Content-Type` 和 `Content-Disposition` 头
- **404 Not Found**: 文档不存在或原始文件已被删除

**响应头:**

```
Content-Type: <根据扩展名推断的 MIME 类型>
Content-Disposition: inline|attachment; filename*=UTF-8''<URL编码的文件名>
```

**实现要点:**

1. 通过 `kb.get_document()` 获取文档信息，读取 `file_path` 字段
2. 使用 `pathlib.Path.exists()` 验证文件是否存在
3. 使用 `mimetypes.guess_type()` 推断 Content-Type
4. 图片/PDF 设置 `inline` 展示，其他类型设置 `attachment` 下载
5. 使用 `urllib.parse.quote()` 对文件名进行 RFC 5987 编码，避免非 ASCII 字符导致 `UnicodeEncodeError`
6. 不传 `filename` 参数给 `FileResponse`，仅通过 `headers` 手动设置 `Content-Disposition`（避免 Starlette 内部重复处理非 ASCII 文件名）

**关键代码:**

```python
@router.get("/documents/{doc_id}/file")
async def download_document_file(
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
) -> FileResponse:
    """下载/查看原始文件。"""
    result = await kb.get_document(session, doc_id)
    if not result:
        raise HTTPException(404, "文档不存在")
    file_path = result.get("file_path")
    if not file_path or not Path(file_path).exists():
        raise HTTPException(404, "原始文件不存在")

    ext = Path(file_path).suffix.lower()
    media_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

    inline_types = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".pdf"}
    disposition = "inline" if ext in inline_types else "attachment"

    filename = result.get("title", "download")
    encoded_filename = quote(filename)

    return FileResponse(
        path=file_path,
        media_type=media_type,
        headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{encoded_filename}"},
    )
```

## 前端设计

### API Client

新增 `getOriginalFileUrl(id)` 方法，返回原始文件 URL（不发起 fetch，直接返回 URL 字符串供 `<img src>` 或 `window.open` 使用）:

```typescript
getOriginalFileUrl(id: string) {
  return `${BASE}/documents/${id}/file`
}
```

### 文档详情页交互

在 `DocumentDetailPage` 头部按钮区域新增"查看原始文件"按钮，位于"编辑"按钮左侧:

```
┌──────────────────────────────────────────────────────────────────┐
│  ←  高勇开题报告.docx  [status]  docx · 12 chunks                │
│                                    [查看原始文件] [编辑] [删除]   │
└──────────────────────────────────────────────────────────────────┘
```

**点击行为:**

```typescript
const handleViewOriginal = () => {
  if (!id) return
  if (doc?.file_type === 'image') {
    setShowOriginal(true)          // 弹窗展示
  } else {
    const url = api.getOriginalFileUrl(id)
    window.open(url, '_blank')     // 新标签页
  }
}
```

### 图片弹窗组件

全屏遮罩 + 居中图片展示，点击遮罩或"✕ 关闭"按钮关闭:

```
┌────────────────────────────────────────────────────┐
│                                                    │
│              ┌─────────────────┐                   │
│              │                 │           ✕ 关闭  │
│              │   原始图片      │                   │
│              │                 │                   │
│              └─────────────────┘                   │
│                                                    │
│              (点击遮罩区域关闭)                      │
└────────────────────────────────────────────────────┘
```

**样式要点:**
- 遮罩: `rgba(0,0,0,0.75)` 全屏 fixed 定位，z-index 1000
- 图片: `max-height: 80vh`，圆角 8px，阴影
- 关闭按钮: 绝对定位在图片右上角外侧

## 涉及文件

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/api/routes.py` | 新增端点 | `GET /documents/{doc_id}/file` — FileResponse 返回原始文件 |
| `frontend/src/api/client.ts` | 新增方法 | `getOriginalFileUrl(id)` — 返回原始文件 URL |
| `frontend/src/pages/DocumentDetailPage.tsx` | 新增功能 | "查看原始文件"按钮 + 图片弹窗组件 |

## 已知限制

1. **无权限控制**: MVP 阶段无认证，任何人可通过 URL 访问任意文档的原始文件
2. **DOCX/PPTX 无法浏览器预览**: 浏览器无原生支持，仅触发下载。如需在线预览可后续集成 Office Online Viewer 或 mammoth.js
3. **文件路径依赖本地存储**: 文件存储在 `uploads/` 目录，如文件被手动删除则返回 404
4. **大文件无分块传输**: 使用 `FileResponse` 一次性返回，超大文件可能占用内存

## 验证

| 测试项 | 方法 | 预期结果 |
|--------|------|---------|
| DOCX 下载 | `GET /documents/{docx_id}/file` | 200, `attachment`, Content-Type 正确 |
| PDF 预览 | `GET /documents/{pdf_id}/file` | 200, `inline`, `application/pdf` |
| 图片展示 | `GET /documents/{img_id}/file` | 200, `inline`, `image/png` 等 |
| 中文文件名 | 检查 Content-Disposition 头 | `filename*=UTF-8''%E9%AB%98...` 正确编码 |
| 文件不存在 | `GET /documents/{nonexistent_id}/file` | 404 |
| 前端按钮 | 点击"查看原始文件" | 图片弹窗 / 新标签页打开 |
