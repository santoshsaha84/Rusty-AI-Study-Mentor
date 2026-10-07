import { useState, useId } from "react";
import { useAuth } from "../../stores/AuthContext";
import { authApi } from "../../api";
import { ApiError } from "../../api/client";
import { exchangeLoginToken } from "../../api/firebase";
import type { AuthUser } from "../../types";

const STUDENT_ID_RE = /^KHEL-\d{4}-[A-Z0-9]{2,6}$/;

export function LoginScreen() {
  const { setUser } = useAuth();
  const [studentId, setStudentId] = useState("");
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const idFieldId = useId();
  const pinFieldId = useId();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!STUDENT_ID_RE.test(studentId.trim())) {
      setError("Student ID format should be KHEL-YYYY-XXX");
      return;
    }
    if (pin.length < 4 || pin.length > 6 || !/^\d+$/.test(pin)) {
      setError("PIN must be 4–6 digits");
      return;
    }

    setLoading(true);
    try {
      const res = await authApi.login(studentId.trim(), pin);
      const user: AuthUser = {
        studentId: studentId.trim(),
        role: res.role as AuthUser["role"],
        classNum: res.class_num,
        centreId: res.centre_id,
        firebaseToken: await exchangeLoginToken(res.firebase_token),
      };
      setUser(user);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 429) setError("Too many attempts. Please wait 15 minutes.");
        else if (err.status === 403) setError("Your account is inactive. Contact your teacher.");
        else setError("Invalid Student ID or PIN. Please try again.");
      } else {
        setError("Rusty is thinking… try again in a moment.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-rusty-sand flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        {/* Logo / Header */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-rusty-green rounded-2xl mb-4">
            <span className="text-white text-3xl font-bold">R</span>
          </div>
          <h1 className="text-2xl font-bold text-rusty-ink">Rusty</h1>
          <p className="text-rusty-muted text-sm mt-1">Your KHEL Study Mentor</p>
        </div>

        {/* Card */}
        <div className="bg-rusty-cream border border-rusty-border rounded-2xl p-6 shadow-sm">
          <form onSubmit={handleSubmit} noValidate className="space-y-4">
            <div>
              <label htmlFor={idFieldId} className="block text-sm font-medium text-rusty-ink mb-1">
                Student ID
              </label>
              <input
                id={idFieldId}
                type="text"
                autoComplete="username"
                placeholder="KHEL-2026-001"
                value={studentId}
                onChange={(e) => setStudentId(e.target.value)}
                maxLength={20}
                required
                className="w-full px-3 py-3 border border-rusty-border rounded-xl text-rusty-ink bg-rusty-sand
                           placeholder:text-rusty-muted focus:outline-none focus:ring-2 focus:ring-rusty-green
                           focus:border-rusty-green text-base"
              />
            </div>

            <div>
              <label htmlFor={pinFieldId} className="block text-sm font-medium text-rusty-ink mb-1">
                PIN
              </label>
              <input
                id={pinFieldId}
                type="password"
                inputMode="numeric"
                autoComplete="current-password"
                placeholder="Enter your PIN"
                value={pin}
                onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 6))}
                maxLength={6}
                required
                className="w-full px-3 py-3 border border-rusty-border rounded-xl text-rusty-ink bg-rusty-sand
                           placeholder:text-rusty-muted focus:outline-none focus:ring-2 focus:ring-rusty-green
                           focus:border-rusty-green text-base tracking-widest"
              />
            </div>

            {error && (
              <div
                role="alert"
                className="bg-rusty-danger-soft border border-rusty-danger rounded-xl px-3 py-2 text-sm text-rusty-danger-dark"
              >
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-3 bg-rusty-green text-white font-semibold rounded-xl
                         hover:bg-rusty-green-dark focus:outline-none focus:ring-2 focus:ring-rusty-green focus:ring-offset-2
                         disabled:opacity-60 disabled:cursor-not-allowed transition-colors text-base min-h-[48px]"
            >
              {loading ? "Signing in…" : "Start Studying"}
            </button>
          </form>
        </div>

        <p className="text-center text-rusty-muted text-xs mt-6">
          Forgot your PIN? Ask your teacher to reset it.
        </p>
      </div>
    </div>
  );
}
