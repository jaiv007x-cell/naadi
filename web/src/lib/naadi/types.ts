export type CaseId = string;

export type Vitals = {
  hr: number;
  sbp: number;
  dbp: number;
  spo2: number;
  rr: number;
  pain: number;
  tempC: number;
};

export type OrderId = string;

export type OrderDef = {
  id: OrderId;
  label: string;
  group: string;
  minutes: number;
  kind: "bedside" | "diagnostic" | "med" | "disposition";
  hint?: string;
};

export type RubricHit = {
  id: string;
  label: string;
  axis: "safety" | "timing" | "diagnosis" | "communication" | "procedure";
  points: number;
  orderId?: OrderId;
  flag?: string;
  required: boolean;
  failCaseOnMiss?: boolean;
  failCaseOnHit?: boolean;
  windowMin?: [number, number];
};

export type CaseBlueprint = {
  id: CaseId;
  version: string;
  title: string;
  specialty: string;
  durationMin: number;
  persona: {
    name: string;
    age: number;
    sex: "M" | "F";
    city: string;
    language: string;
    register: string;
    literacy: string;
    occupation: string;
    summary: string;
    openingLine: string;
    voice: string;
    /** When set, dialogue is proxy history (mother/caregiver speaks for patient). */
    informant?: { name: string; relation: string; age?: number };
  };
  briefing: string;
  hidden: string;
  initialVitals: Vitals;
  orders: OrderDef[];
  rubric: RubricHit[];
  competencies: string[];
  findings: Record<string, string>;
  draft?: boolean;
  contentHash?: string;
  source?: string;
};

export type ChatRole = "student" | "patient" | "system";

export type ChatMessage = {
  id: string;
  role: ChatRole;
  text: string;
  tMin: number;
  at: number;
};

export type LoggedAction = {
  id: string;
  orderId: OrderId;
  label: string;
  tMin: number;
  at: number;
};

export type TraceEvent = {
  id: string;
  tMin: number;
  kind: "order" | "flag" | "vital" | "end" | "chat";
  label: string;
  detail?: string;
};

export type SessionStatus = "briefing" | "running" | "ended";

export type EndReason = "transferred" | "coded" | "abandoned" | "time" | "referral" | "stabilized";

export type GradeResult = {
  score: number;
  max: number;
  pct: number;
  letter: "A" | "B" | "C" | "D" | "F";
  failClosed: boolean;
  passed: boolean;
  overall: number;
  graderVersion: string;
  physioVersion: string;
  evaluationManifestHash: string;
  errorDna: {
    recognition_delay: number;
    action_sequencing: number;
    dose_accuracy: number;
    escalation_timing: number;
    recovery_velocity: number;
    communication_score: number;
  };
  critical: string[];
  hits: {
    id: string;
    label: string;
    axis: RubricHit["axis"];
    points: number;
    awarded: number;
    matched: boolean;
    required: boolean;
    fail: boolean;
    forbidden: boolean;
  }[];
  crs: number;
  competencies: { id: string; score: number }[];
};

export type Session = {
  id: string;
  caseId: CaseId;
  startedAt: number;
  tMin: number;
  status: SessionStatus;
  endReason?: EndReason;
  vitals: Vitals;
  flags: Record<string, boolean>;
  chat: ChatMessage[];
  actions: LoggedAction[];
  events: TraceEvent[];
  findingsSeen: string[];
  grade?: GradeResult;
  /** Live Pratibimb session when engine :8100 is reachable */
  pratibimbSessionId?: string | null;
};

export type RotationStep = {
  id: string;
  label: string;
  result: "pass" | "fail" | "unseen";
};

export type Rotation = {
  id: string;
  procedureId: string;
  label: string;
  at: number;
  preceptor: string;
  steps: RotationStep[];
  competencies: string[];
};

export type GuruTurn = {
  id: string;
  role: "learner" | "guru";
  text: string;
  at: number;
};
