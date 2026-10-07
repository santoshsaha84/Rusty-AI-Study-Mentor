import React, { createContext, useContext, useState, useCallback, useRef } from "react";
import type { AuthUser } from "../types";
import { firebaseSignOut } from "../api/firebase";

interface AuthContextValue {
  user: AuthUser | null;
  setUser: (user: AuthUser | null) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function clearUserStorage(studentId: string) {
  try {
    for (const suffix of ["recent_tests", "recent_study", "recent_taken_quizzes"]) {
      localStorage.removeItem(`rusty:${studentId}:${suffix}`);
    }
  } catch { /* storage unavailable */ }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUserState] = useState<AuthUser | null>(null);
  const userRef = useRef<AuthUser | null>(null);

  const setUser = useCallback((u: AuthUser | null) => {
    userRef.current = u;
    setUserState(u);
  }, []);

  const logout = useCallback(() => {
    if (userRef.current?.studentId) {
      clearUserStorage(userRef.current.studentId);
    }
    userRef.current = null;
    setUserState(null);
    firebaseSignOut().catch(() => { /* already signed out */ });
  }, []);

  return (
    <AuthContext.Provider value={{ user, setUser, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
