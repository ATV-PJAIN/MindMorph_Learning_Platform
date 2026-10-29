// Mirrors the backend learning_session blob (services/learning_service.new_learning_session).
// Kept loose where the backend stores open dicts; only the fields the UI reads are typed.

export type NodeStatus =
  | "available"
  | "in_progress"
  | "mastered"
  | "needs_review"
  | "blocked";

export interface SkillNode {
  id: string;
  label: string;
  description: string;
  level?: "foundational" | "intermediate" | "advanced" | null;
}

export interface SkillEdge {
  source: string;
  target: string;
  relation?: string | null;
}

export interface SkillGraph {
  summary?: string;
  nodes: SkillNode[];
  edges: SkillEdge[];
}

export interface NodeState {
  status: NodeStatus;
  best_score: number;
  attempts: number;
  weaknesses: string[];
  last_feedback: GradeResult | null;
  // Deterministic-lock signal set by a sub-40 grade (mirrors services/mastery.py). Optional for
  // backward-compat with sessions persisted before the field existed.
  remediation_pending?: boolean;
}

export interface GradingArtifact {
  format?: string;
  language?: "python" | "javascript" | "sql" | null;
  unit_tests?: string[];
  sql_assertions?: string[];
  rubric?: { criterion?: string; weight?: number }[];
  instructions?: string;
}

export interface Exercise {
  grounding?: GroundingInfo | null;
  revision?: string;
  format?: "coding_challenge" | "case_study" | null;
  language?: "python" | "javascript" | "sql" | null;
  statement?: string | null;
  grading_artifact?: GradingArtifact | null;
}

export interface Lesson {
  grounding?: GroundingInfo | null;
  content?: string | null;
  exercise?: Exercise;
}

export interface GradeResult {
  // Set when the generated tests could not run at all (missing library, undeclared import, absent
  // data file). No score is reported — the submission was never measured, and nothing about the
  // learner may be inferred from it.
  harness_error?: boolean;
  score?: number;
  passed?: number;
  total?: number;
  failures?: string[];
  stdout?: string;
  timed_out?: boolean;
  per_criterion?: string[];
  feedback?: string;
}

export interface MCQQuestion {
  id?: string;
  node_id: string;
  question: string;
  options: string[];
  correct_index: number;
  explanation?: string;
}

export interface AssessmentQuiz {
  questions: MCQQuestion[];
}

export interface AssessmentResult {
  question_id: string;
  node_id?: string;
  chosen_index: number;
  correct_index: number;
  explanation?: string;
  is_correct: boolean;
}

export interface Assessment {
  quiz: AssessmentQuiz;
  submitted: boolean;
  results?: AssessmentResult[];
}

export interface ChatMessage {
  grounding?: GroundingInfo | null;
  role: "user" | "assistant";
  content: string;
}

// Funnel timeline entry (services/events.py). Loosely typed — only the fields the UI reads.
export interface LearningEvent {
  ts: string;
  stage: string;
  node_id?: string;
  [key: string]: unknown;
}

export interface LearningSession {
  grounding?: GroundingInfo | null;
  skill_graph: SkillGraph;
  summary?: string | null;
  review_passed?: boolean | null;
  review_notes?: string | null;
  format_type?: string;
  domain?: string | null;
  primary_language?: string | null;
  scope?: string | null;
  preferences?: Record<string, string> | null;
  node_state: Record<string, NodeState>;
  lessons: Record<string, Lesson>;
  selected_node?: string | null;
  assessment?: Assessment | null;
  chat?: ChatMessage[];
  events?: LearningEvent[];
}

export interface SessionMeta {
  session_id: string;
  title: string;
  updated_at: string | null;
}

export interface StartSessionResponse {
  grounding?: GroundingInfo | null;
  route: string;
  session_id?: string | null;
  learning_session?: LearningSession | null;
  final_content?: string | null;
  exercise?: Exercise | null;
}

export interface SessionResponse {
  session_id: string;
  learning_session: LearningSession;
  // Ids of any remedial/unlock nodes an adaptation just added (only non-empty on the grade response
  // that triggered it). Optional: only SessionResponse carries it, and older cached payloads won't.
  new_node_ids?: string[];
  // This submission's grade result (grade responses only). Carried separately from
  // node_state.last_feedback, which the backend clears when a failing grade queues a regenerated
  // lesson — without this the learner would never see the score that failed them.
  grade_result?: GradeResult | null;
}

export interface FollowUpQuestion {
  id: string;
  question: string;
  options: string[];
  default: number;
}

export interface FollowUpResponse {
  questions: FollowUpQuestion[];
  detected_domain: string;
  detected_language: string | null;
  detected_specialization: string | null;
}

export interface IngestResponse {
  filename: string;
  chunks: number;
}

export interface GroundingPassage {
  id: string;
  text: string;
  source: string;
  kind: "personal" | "shared" | "web_snippet" | "web_page";
  source_id?: string | null;
  page?: number | null;
  url?: string | null;
  score: number;
}

export interface GroundingInfo {
  status: "grounded" | "insufficient" | "disabled" | "unavailable";
  passages: GroundingPassage[];
  sources: Record<string, string>;
  warnings: string[];
  elapsed_ms: number;
  candidate_count: number;
}
