## ADDED Requirements

### Requirement: Extracted text is always valid UTF-8

Text produced by every supported extractor SHALL contain no unpaired
surrogate code points, so that hashing, persistence, model requests, and API
responses can always encode extracted text as UTF-8. When a malformed
embedded character map causes unpaired surrogates to appear, each SHALL be
replaced with U+FFFD, and the replacement MUST NOT change the length of the
text or the position of any surrounding character.

#### Scenario: PDF with a malformed character map

- **WHEN** a PDF's embedded character map maps a code to an unpaired surrogate
- **THEN** the extracted text contains U+FFFD at that position and document
  hashing, indexing, and persistence all succeed

#### Scenario: Replacement preserves offsets

- **WHEN** an unpaired surrogate is replaced
- **THEN** the extracted text's length is unchanged and every other character
  keeps its original position

#### Scenario: Any supported format is covered

- **WHEN** any supported input format yields unpaired surrogates
- **THEN** the same replacement applies, not only to PDFs

### Requirement: PDFs with recoverable container damage are extracted

A PDF whose textual content is intact SHALL be extracted even when
post-processing by the source damaged the file's trailer, including when the
cross-reference offset and the end-of-file marker end up on a single line.
The unmodified document MUST be attempted first, so a well-formed PDF takes
an unchanged path; repair is a retry, not a replacement.

#### Scenario: Flattened trailer

- **WHEN** a PDF's trailer carries the cross-reference offset and the
  end-of-file marker on one line
- **THEN** the document is extracted successfully instead of failing to open

#### Scenario: Bytes appended after the end-of-file marker

- **WHEN** a non-PDF stamp has been appended after a PDF's end-of-file marker
- **THEN** extraction is unaffected

#### Scenario: Damage that cannot be repaired

- **WHEN** repair is attempted and the document still cannot be read
- **THEN** the document is reported as an extraction failure carrying the
  underlying reason, and no document is created

### Requirement: Extraction failures are classified honestly

A failure originating in the pipeline's own handling of a document SHALL NOT
be reported as damaged or unsupported input. Because encoding errors are
structurally a kind of value error, the upload path MUST NOT let one be
classified as a corrupt file.

#### Scenario: Failure after extraction succeeded

- **WHEN** the pipeline fails while processing already-extracted text
- **THEN** the response identifies a service-side failure rather than
  blaming the file, and the failure is logged with its traceback

#### Scenario: Genuinely unusable input

- **WHEN** the input truly cannot be extracted
- **THEN** the response still names the file as the cause with an actionable
  suggestion

#### Scenario: Edited content carrying an unpaired surrogate

- **WHEN** a content edit submits text containing an unpaired surrogate
- **THEN** the request does not report the document as missing
