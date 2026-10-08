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
  return pages.join('\n\n').trim()
}

async function readResponse(response) {
  const payload = await response.json().catch(() => null)
  if (response.ok) return payload
  const detail = typeof payload?.detail === 'string' ? payload.detail : Array.isArray(payload?.detail) ? payload.detail.map((item) => item.msg).join(' ') : ''
  throw new Error(detail || `The service returned HTTP ${response.status}.`)
}

async function uploadPdf(path, file, signal) {
  const data = new FormData()
  data.append('file', file)
  return readResponse(await fetch(`${API_URL}${path}`, { method: 'POST', body: data, signal }))
}

async function postJson(path, body, signal) {
  return readResponse(await fetch(`${API_URL}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal }))
}

function suggestionsToNotes(suggestions) {
  return String(suggestions || '').split(/\n\s*(?=(?:[-*•]|\d+[.)])\s)|\n{2,}/).map((note) => note.replace(/^\s*(?:[-*•]|\d+[.)])\s*/, '').trim()).filter(Boolean)
    .map((text) => ({ section: 'Suggestion', type: /\b(strength|clear|strong|well|good|match|aligned)\b/i.test(text) ? 'match' : 'gap', text }))
}

function UploadZone({ file, onSelect, disabled, label }) {
  const input = useRef(null)
  const [dragging, setDragging] = useState(false)
  return <><input ref={input} className="sr-only" type="file" accept="application/pdf,.pdf" onChange={(event) => onSelect(event.target.files?.[0])} />
    <button className={`upload-zone ${dragging ? 'is-dragging' : ''} ${file ? 'has-file' : ''}`} type="button" disabled={disabled} onClick={() => input.current?.click()} onDragOver={(event) => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); onSelect(event.dataTransfer.files?.[0]) }}>
      {file ? <><span className="file-tag">PDF</span><span><strong>{file.name}</strong><small>{(file.size / 1024 / 1024).toFixed(1)} MB selected</small></span><span className="change-file">Change</span></> : <><span className="upload-glyph">+</span><span><strong>{label}</strong><small>or browse your files</small></span></>}
    </button></>
}

function DocumentPaper({ text, file, type }) {
  return <article className="resume-paper"><div className="document-topline"><span>{type}</span><span>{file.name}</span></div>
    {type === 'Study material' ? <section className="resume-section"><h2>Source text</h2><p>{text}</p></section> : text.split(/\n\s*\n/).filter(Boolean).map((section, index) => {
      const [firstLine, ...remainingLines] = section.split('\n')
      const hasHeading = remainingLines.length > 0 && firstLine.trim().length < 48
      return <section className="resume-section" key={`${firstLine}-${index}`}>{hasHeading && <h2>{firstLine}</h2>}<p>{(hasHeading ? remainingLines : [firstLine, ...remainingLines]).join(' ')}</p></section>
    })}
  </article>
}

function MarginPanel({ result, status }) {
  if (status === 'resume-reading' || status === 'resume-analyzing') return <aside className="margin-panel loading-panel"><p className="margin-label">Margin review</p><p>{status === 'resume-reading' ? 'Reading your resume…' : 'Comparing against the role…'}</p></aside>
  if (!result) return <aside className="margin-panel intro-notes"><p className="margin-label">Margin review</p><p>Notes will appear here after the document is compared with the role.</p></aside>
  const hasScore = typeof result.score === 'number'
  return <aside className="margin-panel results-panel"><p className="margin-label">Fit assessment</p><div className={`score-stamp ${hasScore && result.score >= 60 ? 'olive' : 'red'}`}><strong>{hasScore ? `${Math.round(result.score)}/100` : 'Review'}</strong><span>{hasScore ? 'role fit' : 'general review'}</span></div>
    {hasScore && <div className="score-breakdown"><div><span>Overlap</span><b>{Math.round(result.score_breakdown?.deterministic_overlap ?? 0)}%</b></div><div><span>LLM assessed</span><b>{Math.round(result.score_breakdown?.llm_assessed ?? 0)}%</b></div></div>}
    <div className="notes">{result.notes.map((note, index) => <article className={`margin-note ${note.type}`} key={`${note.text}-${index}`} style={{ '--delay': `${index * 80}ms` }}><span className="note-dot" /><div><small>{note.section}</small><p>{note.text}</p></div></article>)}</div>
  </aside>
}

function QuizPanel({ quiz, status, onAnswer, onContinue }) {
  const [selected, setSelected] = useState('')
  useEffect(() => setSelected(''), [quiz?.question?.question])
  if (status === 'study-reading' || status === 'study-writing') return <aside className="margin-panel loading-panel"><p className="margin-label">Adaptive quiz</p><p>{status === 'study-reading' ? 'Reading your study material…' : 'Writing the next question…'}</p></aside>
  if (!quiz) return <aside className="margin-panel intro-notes"><p className="margin-label">Adaptive quiz</p><p>Upload a study guide or question book to begin a five-question check of understanding.</p></aside>
  if (quiz.complete) return <aside className="margin-panel quiz-panel"><p className="margin-label">Quiz complete</p><div className="score-stamp olive"><strong>{quiz.correctCount}/{quiz.target}</strong><span>answers correct</span></div><p className="quiz-copy">Use another chapter or restart the quiz to practise a different set of questions.</p></aside>
  if (quiz.feedback) return <aside className="margin-panel quiz-panel"><p className="margin-label">Question {quiz.answeredCount} of {quiz.target}</p><div className={`quiz-feedback ${quiz.feedback.correct ? 'correct' : 'incorrect'}`}><small>{quiz.feedback.correct ? 'Correct' : 'Review'}</small><p>{quiz.feedback.feedback}</p></div><button className="analyze-button" type="button" onClick={onContinue}>{quiz.nextQuestion ? 'Next question' : 'See result'}</button></aside>
  const number = quiz.answeredCount + 1
  return <aside className="margin-panel quiz-panel"><p className="margin-label">Adaptive quiz</p><div className="quiz-progress"><span>Question {number} of {quiz.target}</span><span>{quiz.question.difficulty}</span></div><h2>{quiz.question.question}</h2><p className="quiz-topic">{quiz.question.topic}</p><div className="quiz-choices">{quiz.question.choices.map((choice) => <button type="button" key={choice} className={selected === choice ? 'selected' : ''} onClick={() => setSelected(choice)}>{choice}</button>)}</div><button className="analyze-button" type="button" disabled={!selected} onClick={() => onAnswer(selected)}>Check answer</button></aside>
}

export default function App() {
  const [mode, setMode] = useState('resume')
  const [resumeFile, setResumeFile] = useState(null); const [jobDescription, setJobDescription] = useState(''); const [resumeText, setResumeText] = useState(''); const [result, setResult] = useState(null)
  const [studyFile, setStudyFile] = useState(null); const [studyText, setStudyText] = useState(''); const [quiz, setQuiz] = useState(null)
  const [status, setStatus] = useState('idle'); const [error, setError] = useState(''); const [jdOpen, setJdOpen] = useState(false)
  const busy = status !== 'idle' && status !== 'done'
  const isStudy = mode === 'study'
  const reset = () => { setResumeFile(null); setJobDescription(''); setResumeText(''); setResult(null); setStudyFile(null); setStudyText(''); setQuiz(null); setStatus('idle'); setError(''); setJdOpen(false) }
  useEffect(() => { if (result || quiz) window.scrollTo({ top: 0, behavior: 'smooth' }) }, [result, quiz])
  function selectFile(candidate, setter, label) { if (!candidate) return; if (candidate.type !== 'application/pdf' && !candidate.name.toLowerCase().endsWith('.pdf')) { setter(null); setError(`Choose a PDF ${label} to continue.`); return }; setter(candidate); setError('') }
  async function withTimeout(work) { const controller = new AbortController(); const timeout = window.setTimeout(() => controller.abort(), 120000); try { return await work(controller.signal) } finally { window.clearTimeout(timeout) } }
  function showError(caught, context) { console.error(`ResuMatch ${context} request failed:`, caught); setStatus('idle'); setError(caught.name === 'AbortError' ? 'The service took too long. Check it and try again.' : caught.message || 'The request could not be completed.') }
  async function analyzeResume() {
    if (!resumeFile) { setError('Choose a PDF resume to continue.'); return }
    setError(''); setResult(null); setStatus('resume-reading')
    try { await withTimeout(async (signal) => { const text = await readPdfText(resumeFile); if (!text) throw new Error("Couldn't read that file — try a different PDF."); setResumeText(text); const ingest = await uploadPdf('/ingest-resume', resumeFile, signal); setStatus('resume-analyzing'); const response = await postJson('/analyze', { resume_document_id: ingest.document_id, resume_text: text, jd_text: jobDescription.trim() || null }, signal); setResult({ ...response, notes: suggestionsToNotes(response.suggestions) }); setStatus('done') }) } catch (caught) { showError(caught, 'analysis') }
  }
  async function startQuiz() {
    if (!studyFile) { setError('Choose a PDF study material file to continue.'); return }
    setError(''); setQuiz(null); setStatus('study-reading')
    try { await withTimeout(async (signal) => { const ingest = await uploadPdf('/ingest-study-material', studyFile, signal); setStudyText(ingest.material_text); setStatus('study-writing'); const started = await postJson('/quiz/start', { material_document_id: ingest.document_id, material_text: ingest.material_text, question_count: 5 }, signal); setQuiz({ sessionId: started.session_id, question: started.question, target: started.target_questions, answeredCount: 0, correctCount: 0, feedback: null, nextQuestion: null, complete: false }); setStatus('done') }) } catch (caught) { showError(caught, 'quiz') }
  }
  async function answerQuiz(answer) {
    setError(''); setStatus('study-writing')
    try { await withTimeout(async (signal) => { const response = await postJson('/quiz/answer', { session_id: quiz.sessionId, answer }, signal); setQuiz((current) => ({ ...current, answeredCount: response.answered_count, correctCount: response.correct_count, target: response.target_questions, feedback: response, nextQuestion: response.next_question })); setStatus('done') }) } catch (caught) { showError(caught, 'quiz answer') }
  }
  const continueQuiz = () => setQuiz((current) => current.nextQuestion ? { ...current, question: current.nextQuestion, feedback: null, nextQuestion: null } : { ...current, complete: true, feedback: null })
  const hasOutput = isStudy ? Boolean(quiz) : Boolean(result)
  return <main><header><a className="brand" href="#top">RESUMATCH</a>{hasOutput && <button className="reset-link" onClick={reset}>{isStudy ? 'Start another quiz' : 'Analyze another resume'}</button>}</header><div className="app-shell" id="top"><div className="mode-switch" role="tablist" aria-label="ResuMatch mode"><button className={!isStudy ? 'active' : ''} onClick={() => { reset(); setMode('resume') }}>Resume review</button><button className={isStudy ? 'active' : ''} onClick={() => { reset(); setMode('study') }}>Study test</button></div><div className={`review-layout ${hasOutput ? 'has-results' : ''}`}>{isStudy ? <><section className="document-column">{studyText ? <DocumentPaper text={studyText} file={studyFile} type="Study material" /> : <article className="resume-paper intake-paper"><p className="paper-kicker">Study material under review</p><h1>Test what you have read.</h1><p className="paper-intro">Upload a study guide or question book. The tutor will ask one question at a time and adapt after each answer.</p><UploadZone file={studyFile} onSelect={(file) => selectFile(file, setStudyFile, 'study material')} disabled={busy} label="Drop study material PDF here" />{error && <p className="editorial-error" role="alert">{error}</p>}<button className="analyze-button" type="button" disabled={busy} onClick={startQuiz}>{busy ? (status === 'study-reading' ? 'Reading your study material…' : 'Writing question…') : 'Start study test'}</button></article>}</section><QuizPanel quiz={quiz} status={status} onAnswer={answerQuiz} onContinue={continueQuiz} /></> : <><section className="document-column">{resumeText ? <DocumentPaper text={resumeText} file={resumeFile} type="Resume" /> : <article className="resume-paper intake-paper"><p className="paper-kicker">Document under review</p><h1>Start with the resume.</h1><p className="paper-intro">The review will mark what supports the role and where the evidence is thin.</p><UploadZone file={resumeFile} onSelect={(file) => selectFile(file, setResumeFile, 'resume')} disabled={busy} label="Drop a resume PDF here" /><div className="jd-field"><button type="button" className="jd-toggle" onClick={() => setJdOpen(!jdOpen)}>Job description <span>Optional {jdOpen ? '−' : '+'}</span></button>{jdOpen && <textarea id="jd" value={jobDescription} onChange={(event) => setJobDescription(event.target.value)} placeholder="Optional — paste the job description for a role-specific review." rows="6" disabled={busy} />}</div>{error && <p className="editorial-error" role="alert">{error}</p>}<button className="analyze-button" type="button" disabled={busy} onClick={analyzeResume}>{busy ? (status === 'resume-reading' ? 'Reading your resume…' : 'Comparing against the role…') : 'Analyze document'}</button></article>}</section><MarginPanel result={result} status={status} /></>}</div></div></main>
}
