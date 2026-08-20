import { useEffect, useRef, useState } from 'react'
import { getDocument, GlobalWorkerOptions } from 'pdfjs-dist'
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

GlobalWorkerOptions.workerSrc = pdfWorker

const API_URL = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '')

async function readPdfText(file) {
  const pdf = await getDocument({ data: await file.arrayBuffer() }).promise
  const pages = await Promise.all(Array.from({ length: pdf.numPages }, async (_, index) => {
    const page = await pdf.getPage(index + 1)
    const content = await page.getTextContent()
    return content.items.map((item) => item.str).join(' ')
  }))
  const text = pages.join('\n\n').trim()
  if (!text) throw new Error("Couldn't read that file — try a different PDF.")
  return text
}

async function readResponse(response) {
  const payload = await response.json().catch(() => null)
  if (response.ok) return payload
  const detail = typeof payload?.detail === 'string'
    ? payload.detail
    : Array.isArray(payload?.detail) ? payload.detail.map((item) => item.msg).join(' ') : ''
  throw new Error(detail || `The review service returned HTTP ${response.status}.`)
}

async function postIngest(file, signal) {
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetch(`${API_URL}/ingest-resume`, { method: 'POST', body: formData, signal })
  return readResponse(response)
}

async function postAnalyze({ documentId, resumeText, jobDescription, signal }) {
  const response = await fetch(`${API_URL}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      resume_document_id: documentId,
      resume_text: resumeText,
      jd_text: jobDescription.trim() || null,
    }),
    signal,
  })
  return readResponse(response)
}

function suggestionsToNotes(suggestions) {
  const notes = String(suggestions || '')
    .split(/\n\s*(?=(?:[-*•]|\d+[.)])\s)|\n{2,}/)
    .map((note) => note.replace(/^\s*(?:[-*•]|\d+[.)])\s*/, '').trim())
    .filter(Boolean)

  return notes.map((text) => ({
    section: 'Suggestion',
    type: /\b(strength|clear|strong|well|good|match|aligned)\b/i.test(text) ? 'match' : 'gap',
    text,
  }))
}

function UploadZone({ file, onSelect, disabled }) {
  const input = useRef(null)
  const [dragging, setDragging] = useState(false)
  const choose = (candidate) => onSelect(candidate)
  return <><input ref={input} className="sr-only" type="file" accept="application/pdf,.pdf" onChange={(event) => choose(event.target.files?.[0])} />
    <button className={`upload-zone ${dragging ? 'is-dragging' : ''} ${file ? 'has-file' : ''}`} type="button" disabled={disabled} onClick={() => input.current?.click()}
      onDragOver={(event) => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); choose(event.dataTransfer.files?.[0]) }}>
      {file ? <><span className="file-tag">PDF</span><span><strong>{file.name}</strong><small>{(file.size / 1024 / 1024).toFixed(1)} MB selected</small></span><span className="change-file">Change</span></> : <><span className="upload-glyph">+</span><span><strong>Drop a resume PDF here</strong><small>or browse your files</small></span></>}
    </button></>
}

function ResumePaper({ text, file }) {
  return <article className="resume-paper"><div className="document-topline"><span>Resume</span><span>{file.name}</span></div>
    {text.split(/\n\s*\n/).filter(Boolean).map((section, index) => {
      const [firstLine, ...remainingLines] = section.split('\n')
      const hasShortHeading = remainingLines.length > 0 && firstLine.trim().length < 48
      return <section className="resume-section" key={`${firstLine}-${index}`}>
        {hasShortHeading && <h2>{firstLine}</h2>}
        <p>{(hasShortHeading ? remainingLines : [firstLine, ...remainingLines]).join(' ')}</p>
      </section>
    })}
  </article>
}

function MarginPanel({ result, status }) {
  if (status === 'reading' || status === 'comparing') return <aside className="margin-panel loading-panel"><p className="margin-label">Margin review</p><p>{status === 'reading' ? 'Reading your resume…' : 'Comparing against the role…'}</p></aside>
  if (!result) return <aside className="margin-panel intro-notes"><p className="margin-label">Margin review</p><p>Notes will appear here after the document is compared with the role.</p></aside>
  const hasScore = typeof result.score === 'number'
  const tone = hasScore && result.score >= 60 ? 'olive' : 'red'
  return <aside className="margin-panel results-panel"><p className="margin-label">Fit assessment</p><div className={`score-stamp ${tone}`}><strong>{hasScore ? `${Math.round(result.score)}/100` : 'Review'}</strong><span>{hasScore ? 'role fit' : 'general review'}</span></div>
    {hasScore && <div className="score-breakdown"><div><span>Overlap</span><b>{Math.round(result.score_breakdown?.deterministic_overlap ?? 0)}%</b></div><div><span>LLM assessed</span><b>{Math.round(result.score_breakdown?.llm_assessed ?? 0)}%</b></div></div>}
    <div className="notes">{result.notes.map((note, index) => <article className={`margin-note ${note.type}`} key={note.text} style={{ '--delay': `${index * 80}ms` }}><span className="note-dot" /><div><small>{note.section}</small><p>{note.text}</p></div></article>)}</div>
  </aside>
}

export default function App() {
  const [file, setFile] = useState(null); const [jobDescription, setJobDescription] = useState(''); const [resumeText, setResumeText] = useState(''); const [result, setResult] = useState(null); const [status, setStatus] = useState('idle'); const [error, setError] = useState(''); const [jdOpen, setJdOpen] = useState(false)
  const busy = status === 'reading' || status === 'comparing'
  useEffect(() => { if (result) window.scrollTo({ top: 0, behavior: 'smooth' }) }, [result])
  function selectFile(candidate) { if (!candidate) return; if (candidate.type !== 'application/pdf' && !candidate.name.toLowerCase().endsWith('.pdf')) { setFile(null); setError('Choose a PDF resume to continue.'); return }; setFile(candidate); setError('') }
  async function analyze() {
    if (!file) { setError('Choose a PDF resume to continue.'); return }
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 120000)
    setError('')
    setResult(null)
    setStatus('reading')
    try {
      const resumeText = await readPdfText(file)
      setResumeText(resumeText)
      const ingest = await postIngest(file, controller.signal)
      setStatus('comparing')
      const response = await postAnalyze({ documentId: ingest.document_id, resumeText, jobDescription, signal: controller.signal })
      setResult({ ...response, notes: suggestionsToNotes(response.suggestions) })
      setStatus('done')
    } catch (caught) {
      console.error('ResuMatch analysis request failed:', caught)
      setStatus('idle')
      setError(caught.name === 'AbortError' ? 'The review took too long. Check the service and try again.' : caught.message || 'The review could not be completed.')
    } finally {
      window.clearTimeout(timeout)
    }
  }
  function reset() { setFile(null); setJobDescription(''); setResumeText(''); setResult(null); setStatus('idle'); setError(''); setJdOpen(false) }
  return <main><header><a className="brand" href="#top">RESUMATCH</a>{result && <button className="reset-link" onClick={reset}>Analyze another resume</button>}</header><div className="app-shell" id="top"><div className={`review-layout ${result ? 'has-results' : ''}`}><section className="document-column">{resumeText ? <ResumePaper text={resumeText} file={file} /> : <article className="resume-paper intake-paper"><p className="paper-kicker">Document under review</p><h1>Start with the resume.</h1><p className="paper-intro">The review will mark what supports the role and where the evidence is thin.</p><UploadZone file={file} onSelect={selectFile} disabled={busy} /><div className="jd-field"><button type="button" className="jd-toggle" onClick={() => setJdOpen(!jdOpen)}>Job description <span>Optional {jdOpen ? '−' : '+'}</span></button>{jdOpen && <textarea id="jd" value={jobDescription} onChange={(event) => setJobDescription(event.target.value)} placeholder="Optional — paste the job description for a role-specific review." rows="6" disabled={busy} />}</div>{error && <p className="editorial-error" role="alert">{error}</p>}<button className="analyze-button" type="button" disabled={busy} onClick={analyze}>{busy ? (status === 'reading' ? 'Reading your resume…' : 'Comparing against the role…') : 'Analyze document'}</button></article>}</section><MarginPanel result={result} status={status} /></div></div></main>
}
