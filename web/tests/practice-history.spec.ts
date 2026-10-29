import { expect, test } from "@playwright/test";
import { practiceAttempts } from "../components/PracticeHistory";

const base = () => ({
  session_id: "history",
  learning_session: {
    summary: "Practice history",
    skill_graph: { nodes: [
      { id: "a", label: "Chords", description: "", level: "foundational" },
      { id: "b", label: "Rhythm", description: "", level: "foundational" },
    ], edges: [] },
    selected_node: "a",
    node_state: { a: { status: "available", best_score: 0, attempts: 0, weaknesses: [], last_feedback: null }, b: { status: "available", best_score: 0, attempts: 0, weaknesses: [], last_feedback: null } },
    lessons: { a: { content: "Lesson", exercise: { revision: "rev1", format: "case_study", statement: "Explain." } } },
    events: [] as unknown[],
  },
});

test("derives only finite in-range grade events for the requested skill in reverse append order", () => {
  const source = [
    { stage: "exercise_graded", node_id: "a", score: 0 },
    { stage: "exercise_graded", node_id: "a", score: 20, ts: "2026-01-01T00:00:00Z" },
    { stage: "exercise_graded", node_id: "b", score: 30 },
    { stage: "lesson_opened", node_id: "a", score: 50 },
    { stage: "exercise_graded", node_id: "a", score: 90 },
    { stage: "exercise_graded", node_id: "a", score: 100 },
    { stage: "grade_failure", node_id: "a", score: 44 },
    { stage: "assessment_submitted", node_id: "a", score: 55 },
    { stage: "exercise_graded", node_id: "a", score: "80" },
    { stage: "exercise_graded", node_id: "a", score: NaN },
    { stage: "exercise_graded", node_id: "a", score: Infinity },
    { stage: "exercise_graded", node_id: "a", score: -1 },
    { stage: "exercise_graded", node_id: "a", score: 101 },
    { stage: "exercise_graded", node_id: "a" },
    { stage: "exercise_graded", node_id: "a", score: { value: 30 } },
    null,
  ];
  const before = [...source];
  const attempts = practiceAttempts(source, "a");
  expect(attempts).toEqual([{ score: 100, ts: undefined }, { score: 90, ts: undefined }, { score: 20, ts: "2026-01-01T00:00:00Z" }, { score: 0, ts: undefined }]);
  expect(source).toEqual(before);
  expect(practiceAttempts(undefined, "a")).toEqual([]);
});

test("renders history, expands accessibly, resets on skill change, and fits mobile", async ({ page }) => {
  const payload = base();
  payload.learning_session.events = Array.from({ length: 7 }, (_, i) => ({ stage: "exercise_graded", node_id: "a", score: i * 10, ...(i === 5 ? {} : { ts: i === 6 ? "bad-date" : `2026-06-0${i + 1}T12:00:00Z` }) }));
  await page.addInitScript(() => localStorage.setItem("mindmorph.userId", "history@test.com"));
  let selectedNode = "a";
  await page.route("**/sessions/**", async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === "POST" && url.pathname.includes("/lessons/")) selectedNode = url.pathname.split("/").pop()!;
    const response = { ...payload, learning_session: { ...payload.learning_session, selected_node: selectedNode } };
    return route.fulfill({ json: response });
  });
  await page.setViewportSize({ width: 375, height: 700 });
  await page.goto("/session/history");
  const history = page.getByRole("region", { name: "Practice history: Chords" });
  await expect(history.getByRole("row")).toHaveCount(6);
  await expect(history.getByText("Date unavailable")).toHaveCount(2);
  const toggle = history.getByRole("button", { name: "Show all" });
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(history.getByRole("button", { name: "Show less" })).toHaveAttribute("aria-expanded", "true");
  await expect(history.getByRole("row")).toHaveCount(8);
  const validDate = await page.evaluate(() => new Date("2026-06-01T12:00:00Z").toLocaleString());
  await expect(history.getByText(validDate)).toBeVisible();
  await expect(history.getByText("Date unavailable")).toHaveCount(2);
  await history.getByRole("button", { name: "Show less" }).click();
  await expect(history.getByRole("row")).toHaveCount(6);
  await expect(history.getByRole("button", { name: "Show all" })).toHaveAttribute("aria-expanded", "false");
  await history.getByRole("button", { name: "Show all" }).click();
  const rhythm = page.getByRole("button", { name: /^Rhythm/ });
  await rhythm.focus();
  await page.keyboard.press("Enter");
  const other = page.getByRole("region", { name: "Practice history: Rhythm" });
  await expect(other.getByText("No recorded practice attempts yet")).toBeVisible();
  await expect(other.getByRole("button", { name: "Show less" })).toHaveCount(0);
  await page.getByRole("button", { name: /Chords/ }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("region", { name: "Practice history: Chords" }).getByRole("row")).toHaveCount(6);
  await expect(page.getByRole("region", { name: "Practice history: Chords" }).getByRole("button", { name: "Show all" })).toHaveAttribute("aria-expanded", "false");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("grade appears from session cache writeback after lesson cache clears and survives reload", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mindmorph.userId", "history@test.com"));
  let saved = base();
  await page.route("**/sessions/**", async (route) => {
    if (route.request().method() === "POST" && route.request().url().includes("/grade?")) {
      saved = { ...saved, learning_session: { ...saved.learning_session, lessons: {}, events: [{ stage: "exercise_graded", node_id: "a", score: 70, ts: "2026-10-21T10:00:00Z" }] } };
      return route.fulfill({ json: { ...saved, grade_result: { score: 70, feedback: "Keep practicing." } } });
    }
    return route.fulfill({ json: saved });
  });
  await page.goto("/session/history");
  await page.getByLabel("Your response").fill("My answer");
  await page.getByRole("button", { name: "Grade my submission" }).click();
  const history = page.getByRole("region", { name: "Practice history: Chords" });
  await expect(history.getByText("70%", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("region", { name: "Practice history: Chords" }).getByText("70%", { exact: true })).toBeVisible();
});

test("a grading harness failure preserves prior attempts and adds no scored history entry", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mindmorph.userId", "history@test.com"));
  const saved = base();
  saved.learning_session.events = [{ stage: "exercise_graded", node_id: "a", score: 55, ts: "2026-10-20T10:00:00Z" }];
  await page.route("**/sessions/**", async (route) => {
    if (route.request().method() === "POST" && route.request().url().includes("/grade?")) {
      return route.fulfill({ json: { ...saved, grade_result: { harness_error: true, feedback: "The checks could not run." } } });
    }
    return route.fulfill({ json: saved });
  });
  await page.goto("/session/history");
  await page.getByLabel("Your response").fill("My answer");
  await page.getByRole("button", { name: "Grade my submission" }).click();
  const history = page.getByRole("region", { name: "Practice history: Chords" });
  await expect(history.getByText("55%", { exact: true })).toBeVisible();
  await expect(history.getByRole("row")).toHaveCount(2);
});

test("older sessions without events show the exact empty state", async ({ page }) => {
  const payload = base();
  delete payload.learning_session.events;
  await page.addInitScript(() => localStorage.setItem("mindmorph.userId", "history@test.com"));
  await page.route("**/sessions/**", (route) => route.fulfill({ json: payload }));
  await page.goto("/session/history");
  await expect(page.getByText("No recorded practice attempts yet", { exact: true })).toBeVisible();
});
