import { test, expect, type Page } from "@playwright/test";

function learningSession() {
  return {
    skill_graph: { summary: "Learn guitar", nodes: [{ id: "a", label: "Chords", description: "Basic chords", level: "foundational" }], edges: [] },
    summary: "Learn guitar", node_state: { a: { status: "available", best_score: 0, attempts: 0, weaknesses: [], last_feedback: null } },
    lessons: { a: { content: "# Chord lesson", exercise: { revision: "exercise-v2", format: "case_study", statement: "Explain this chord progression." } } },
    selected_node: "a",
  };
}

async function prepare(page: Page, mode: "normal" | "failure" | "empty" = "normal") {
  await page.addInitScript(() => localStorage.setItem("mindmorph.userId", "recovery@test.com"));
  await page.route("**/sessions/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/sessions/followup") {
      if (mode === "failure") return route.fulfill({ status: 503, json: { detail: "busy" } });
      return route.fulfill({ json: { questions: mode === "empty" ? [] : [{ id: "scope", question: "Choose your scope", options: ["focused", "foundation"], default: 0 }], detected_domain: "Music", detected_language: null } });
    }
    if (path === "/sessions/stream") return route.fulfill({ contentType: "text/event-stream", body: `data: ${JSON.stringify({ done: true, session: { route: "SCOUT", session_id: "recovery", learning_session: learningSession() } })}\n\n` });
    if (path.endsWith("/recovery")) return route.fulfill({ json: { session_id: "recovery", learning_session: learningSession() } });
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.getByLabel("What do you want to learn?").fill("Learn guitar");
}

test("Skip All continues with empty answers instead of cancelling", async ({ page }) => {
  await prepare(page);
  await page.getByRole("button", { name: "Generate", exact: true }).click();
  const request = page.waitForRequest((r) => r.url().endsWith("/sessions/stream"));
  await page.getByRole("button", { name: "Skip All" }).click();
  expect((await request).postDataJSON().preferences).toEqual({});
  await expect(page.getByRole("dialog", { name: "Your learning path" })).toBeVisible();
});

for (const mode of ["failure", "empty"] as const) {
  test(`follow-up ${mode} offers continuation and reaches a path`, async ({ page }) => {
    await prepare(page, mode);
    await page.getByRole("button", { name: "Generate", exact: true }).click();
    await page.getByRole("button", { name: "Continue without questions" }).click();
    await expect(page.getByRole("dialog", { name: "Your learning path" })).toBeVisible();
    await page.keyboard.press("Escape");
    await page.waitForURL("**/session/recovery");
    await expect(page.getByText("skills complete")).toBeVisible();
  });
}

test("preference dialog contains keyboard focus, fits mobile and returns focus on Escape", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 520 });
  await prepare(page);
  const trigger = page.getByRole("button", { name: "Generate", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Learning preferences" });
  await expect(dialog).toBeVisible();
  await expect.poll(() => dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press("Tab");
    expect(await dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);
  }
  const bounds = await dialog.boundingBox();
  expect(bounds!.height).toBeLessThanOrEqual(488);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("grading sends the displayed exercise revision and keeps the answer after a save error", async ({ page }) => {
  await prepare(page);
  await page.route("**/sessions/*/recovery/grade**", (route) => route.fulfill({ status: 503, json: { detail: "Progress could not be saved. Please try again." } }));
  await page.goto("/session/recovery");
  await page.getByLabel("Your response").fill("My chord explanation");
  const submission = page.waitForRequest((r) => r.url().includes("/grade?"));
  await page.getByRole("button", { name: "Grade my submission" }).click();
  expect((await submission).postDataJSON().exercise_revision).toBe("exercise-v2");
  await expect(page.getByRole("alert").filter({ hasText: "could not be saved" })).toBeVisible();
  await expect(page.getByLabel("Your response")).toHaveValue("My chord explanation");
  await expect(page.getByText("0/1")).toBeVisible();
});

test("saved assessment results can be reopened after refresh using question identifiers", async ({ page }) => {
  await prepare(page);
  await page.route("**/sessions/*/recovery", (route) => route.fulfill({ json: {
    session_id: "recovery", learning_session: { ...learningSession(), assessment: {
      submitted: true, quiz: { questions: [{ id: "q1", node_id: "a", question: "Which chord?", options: ["C", "D"], correct_index: 0 }] },
      results: [{ question_id: "q1", chosen_index: 0, correct_index: 0, is_correct: true }],
    } },
  } }));
  await page.goto("/session/recovery");
  await page.reload();
  await page.getByRole("button", { name: "Review assessment" }).click();
  await expect(page.getByText("1 of 1 Correct")).toBeVisible();
  await expect(page.getByText("Which chord?")).toBeVisible();
  await expect(page.getByText(/head-start/)).toBeVisible();
});
