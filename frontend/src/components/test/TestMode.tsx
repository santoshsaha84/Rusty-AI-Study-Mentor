import { useState, useEffect, useRef } from "react";
import { useAuth } from "../../stores/AuthContext";
import { testApi } from "../../api";
import { MathRenderer } from "../shared/MathRenderer";
import { TestGenLoader } from "../shared/AILoader";
import { addRecentTest } from "../../stores/recentActivity";
import type { Subject, TestQuestion, TestAnswerResponse, TestResult } from "../../types";

type Phase = "loading" | "question" | "feedback" | "result" | "error";

interface Props {
  subject: Subject;
  chapter: string | null;
  medium: "en" | "hi";
  onBack: () => void;
  onTryAgain: () => void;
  onStudyWeakTopics: (subject: Subject, chapter: string | null) => void;
  resumeTestId?: string;
}

const OPTION_LETTERS = ["A", "B", "C", "D"] as const;

export function TestMode({ subject, chapter, medium, onBack, onTryAgain, onStudyWeakTopics, resumeTestId }: Props) {
  const { user } = useAuth();
  const [phase, setPhase] = useState<Phase>("loading");
  const [testId, setTestId] = useState<string | null>(null);
  const [questions, setQuestions] = useState<TestQuestion[]>([]);
  const [currentIdx, setCurrentIdx] = useState(0);
  const [selectedOption, setSelectedOption] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<TestAnswerResponse | null>(null);
  const [result, setResult] = useState<TestResult | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [loaderStep, setLoaderStep] = useState(0);
  const [scoreSoFar, setScoreSoFar] = useState(0);

  const loaderRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (phase === "loading") {
      loaderRef.current = setInterval(() => setLoaderStep((s) => s + 1), 1000);
    } else {
      if (loaderRef.current) clearInterval(loaderRef.current);
    }
    return () => { if (loaderRef.current) clearInterval(loaderRef.current); };
  }, [phase]);

  useEffect(() => {
    if (!user) return;
    const ac = new AbortController();

    // Defer the request one tick: React StrictMode (dev) mounts, unmounts and
    // remounts immediately, and aborting a fetch doesn't stop the backend — without
    // this, every test start would make the LLM generate two tests.
    const start = setTimeout(() => {
      if (resumeTestId) {
        testApi
          .resume(resumeTestId, user.firebaseToken)
          .then((res) => {
            if (ac.signal.aborted) return;
            setTestId(res.test_id);
            setQuestions(res.questions.map((q) => ({
              question_id: q.question_id,
              question_no: q.question_no,
              question_text: q.question_text,
              options: q.options,
              has_math: q.has_math,
            })));
            setCurrentIdx(res.current_index);
            setScoreSoFar(res.score_so_far);

            if (res.current_index >= res.questions.length) {
              testApi.result(res.test_id, user.firebaseToken).then((r) => {
                setResult(r);
                setPhase("result");
              }).catch(() => setPhase("error"));
            } else {
              setPhase("question");
            }
          })
          .catch((err) => {
            if (ac.signal.aborted) return;
            setErrorMsg("Could not load test. Please try again.");
            setPhase("error");
          });
      } else {
        testApi
          .generate(subject, chapter, user.firebaseToken, medium, ac.signal)
          .then((res) => {
            if (ac.signal.aborted) return;
            setTestId(res.test_id);
            setQuestions(res.questions);
            setPhase("question");
          })
          .catch((err) => {
            if (ac.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) return;
            setErrorMsg("Could not generate test. Please try again.");
            setPhase("error");
          });
      }
    }, 0);

    return () => { clearTimeout(start); ac.abort(); };
  }, []);

  async function handleAnswer(option: string) {
    if (!testId || !user || phase === "feedback") return;
    const q = questions[currentIdx];
    setSelectedOption(option);
    setPhase("feedback");

    try {
      const res = await testApi.answer(testId, q.question_id, option, user.firebaseToken);
      setFeedback(res);
      setScoreSoFar(res.score_so_far);
      if (res.questions_remaining === 0) {
        const resultRes = await testApi.result(testId, user.firebaseToken);
        setResult(resultRes);
        addRecentTest(user.studentId, {
          subject,
          chapter,
          score: resultRes.score,
          total: resultRes.total,
          source: "practice",
        });
      }
    } catch {
      setFeedback(null);
    }
  }

  function handleNext() {
    if (!feedback) return;
    if (feedback.questions_remaining === 0 && result) {
      setPhase("result");
      return;
    }
    setCurrentIdx((i) => i + 1);
    setSelectedOption(null);
    setFeedback(null);
    setPhase("question");
  }

  const currentQ = questions[currentIdx];
  const progress = questions.length > 0 ? (currentIdx / questions.length) * 100 : 0;
  const pct = result ? Math.round((result.score / result.total) * 100) : 0;
  const isPass = pct >= 70;
  const subjectLabel = subject.replace(/_/g, " ");

  return (
    <div className="min-h-screen bg-rusty-sand flex flex-col">
      <header className="bg-rusty-green text-white px-4 py-3 flex items-center gap-3 sticky top-0 z-10">
        <button onClick={onBack}
          className="text-white/80 hover:text-white focus:outline-none focus:ring-2 focus:ring-white
                     rounded min-w-[44px] min-h-[44px] flex items-center justify-center -ml-1"
          aria-label="Back">←</button>
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <p className="font-semibold text-sm capitalize">{subjectLabel} Test</p>
            <span className="text-[10px] bg-rusty-ai/80 text-white rounded px-1.5 py-0.5 font-bold">AI</span>
          </div>
          {(phase === "question" || phase === "feedback") && (
            <p className="text-white/70 text-xs">Question {currentIdx + 1} of {questions.length}</p>
          )}
        </div>
        {(phase === "question" || phase === "feedback") && (
          <span className="text-xs bg-white/20 rounded-full px-2.5 py-0.5 flex-shrink-0">
            {feedback?.score_so_far ?? scoreSoFar}/{currentIdx + (phase === "feedback" ? 1 : 0)}
          </span>
        )}
      </header>

      {(phase === "question" || phase === "feedback") && (
        <div className="h-1 bg-rusty-green-soft">
          <div className="h-full bg-rusty-green transition-all duration-300" style={{ width: `${progress}%` }} />
        </div>
      )}

      <div className="flex-1 px-4 py-6">
        {phase === "loading" && <TestGenLoader step={loaderStep} />}

        {phase === "error" && (
          <div className="space-y-4">
            <div className="bg-rusty-danger-soft border border-rusty-danger rounded-xl p-4 text-rusty-danger-dark text-sm">
              {errorMsg}
            </div>
            <button onClick={onBack}
              className="w-full py-3 border border-rusty-border rounded-xl text-rusty-ink text-sm min-h-[48px]">
              Back to Home
            </button>
          </div>
        )}

        {(phase === "question" || phase === "feedback") && currentQ && (
          <div className="space-y-4">
            <div className="bg-rusty-cream border border-rusty-border rounded-2xl p-4">
              <p className="text-xs text-rusty-muted mb-1.5">Q{currentQ.question_no}.</p>
              <p className="text-rusty-ink font-medium text-base leading-snug">
                <MathRenderer text={currentQ.question_text} />
              </p>
            </div>

            <div className="space-y-3">
              {OPTION_LETTERS.map((letter) => {
                const text = currentQ.options[letter];
                const isSelected = selectedOption === letter;
                const isCorrect = feedback?.correct_option === letter;
                const isWrong = isSelected && !feedback?.is_correct;

                let cls = "w-full flex items-start gap-3 p-4 rounded-xl border text-left transition-colors min-h-[52px] focus:outline-none focus:ring-2 focus:ring-rusty-green focus:ring-offset-2 ";
                if (phase === "feedback") {
                  if (isCorrect) cls += "bg-rusty-success-soft border-rusty-success text-rusty-success-dark";
                  else if (isWrong) cls += "bg-rusty-danger-soft border-rusty-danger text-rusty-danger-dark";
                  else cls += "bg-rusty-cream border-rusty-border text-rusty-muted";
                } else {
                  cls += isSelected
                    ? "bg-rusty-green-soft border-rusty-green text-rusty-green-dark"
                    : "bg-rusty-cream border-rusty-border text-rusty-ink hover:border-rusty-green-dark";
                }

                return (
                  <button key={letter} onClick={() => handleAnswer(letter)}
                    disabled={phase === "feedback"} className={cls}>
                    <span className="font-bold text-sm w-5 flex-shrink-0 mt-0.5">{letter}.</span>
                    <span className="text-sm leading-snug flex-1"><MathRenderer text={text} /></span>
                    {phase === "feedback" && isCorrect && <span className="ml-auto text-rusty-success text-xs font-bold flex-shrink-0">✓</span>}
                    {phase === "feedback" && isWrong && <span className="ml-auto text-rusty-danger text-xs font-bold flex-shrink-0">✗</span>}
                  </button>
                );
              })}
            </div>

            {phase === "feedback" && feedback && (
              <div className={`rounded-2xl p-4 space-y-3 border
                ${feedback.is_correct ? "bg-rusty-success-soft border-rusty-success/30" : "bg-rusty-danger-soft border-rusty-danger/30"}`}>
                <p className={`font-semibold text-sm ${feedback.is_correct ? "text-rusty-success-dark" : "text-rusty-danger-dark"}`}>
                  {feedback.is_correct ? "✓ Correct!" : `✗ Incorrect — answer is ${feedback.correct_option}`}
                </p>
                {feedback.explanation && (
                  <p className="text-rusty-ink text-sm leading-relaxed">
                    <MathRenderer text={feedback.explanation} />
                  </p>
                )}
                <button onClick={handleNext}
                  className="w-full py-3 bg-rusty-green text-white font-semibold rounded-xl
                             hover:bg-rusty-green-dark focus:outline-none focus:ring-2 focus:ring-rusty-green
                             focus:ring-offset-2 transition-colors text-sm min-h-[48px]">
                  {feedback.questions_remaining === 0 ? "See Results →" : "Next Question →"}
                </button>
              </div>
            )}
          </div>
        )}

        {phase === "result" && result && (
          <div className="space-y-5 max-w-md mx-auto">
            <div className={`rounded-2xl p-6 text-center border ${isPass ? "bg-rusty-success-soft border-rusty-success/20" : "bg-rusty-warning-soft border-rusty-warning/20"}`}>
              <div className="flex justify-center mb-2">
                <span className="text-[11px] bg-rusty-ai-soft text-rusty-ai px-2.5 py-0.5 rounded-full font-bold">AI Generated Practice Test</span>
              </div>
              <div className="text-5xl font-bold text-rusty-ink mb-1 tracking-tight">{result.score}/{result.total}</div>
              <div className={`text-2xl font-bold mb-2 ${isPass ? "text-rusty-success-dark" : "text-rusty-warning-dark"}`}>{pct}%</div>
              <p className={`text-sm font-medium ${isPass ? "text-rusty-success-dark" : "text-rusty-warning-dark"}`}>
                {isPass ? "Great work — keep it up!" : "Good effort — a little more practice!"}
              </p>
            </div>

            {(result.strong_topics ?? []).length > 0 && (
              <div>
                <p className="text-xs font-semibold text-rusty-ink mb-2 uppercase tracking-wide">Strong topics</p>
                <div className="flex flex-wrap gap-2">
                  {(result.strong_topics ?? []).map((topic, i) => (
                    <span key={i} className="bg-rusty-success-soft text-rusty-success-dark text-xs px-3 py-1.5 rounded-full border border-rusty-success/20 font-medium">
                      ✓ {topic}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {result.weak_topics.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-rusty-ink mb-2 uppercase tracking-wide">Review these</p>
                <div className="flex flex-wrap gap-2">
                  {result.weak_topics.map((topic, i) => (
                    <span key={i} className="bg-rusty-warning-soft text-rusty-warning-dark text-xs px-3 py-1.5 rounded-full border border-rusty-warning/20 font-medium">
                      {topic}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div className="space-y-3 pt-1">
              {result.weak_topics.length > 0 && (
                <button onClick={() => onStudyWeakTopics(subject, result.weak_topics[0])}
                  className="w-full py-3.5 bg-rusty-ai text-white font-semibold rounded-xl
                             hover:bg-rusty-ai-dark focus:outline-none focus:ring-2 focus:ring-rusty-ai
                             focus:ring-offset-2 transition-colors text-sm min-h-[52px]">
                  Study weak topics with Rusty
                </button>
              )}
              <button onClick={onTryAgain}
                className="w-full py-3.5 bg-rusty-green text-white font-semibold rounded-xl
                           hover:bg-rusty-green-dark focus:outline-none focus:ring-2 focus:ring-rusty-green
                           focus:ring-offset-2 transition-colors text-sm min-h-[52px]">
                Try Again
              </button>
              <button onClick={onBack}
                className="w-full py-3.5 border border-rusty-border bg-rusty-cream text-rusty-ink
                           font-semibold rounded-xl hover:border-rusty-green focus:outline-none
                           focus:ring-2 focus:ring-rusty-green focus:ring-offset-2 transition-colors text-sm min-h-[52px]">
                Back to Home
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
