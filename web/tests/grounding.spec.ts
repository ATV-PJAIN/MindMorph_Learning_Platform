import { expect, test } from "@playwright/test";

function evidence(status = "grounded") {
  return { status, passages: [
    { id: "p_private", text: "SQL joins connect matching rows", source: "Study notes.pdf", kind: "personal", page: 3, score: 0.9 },
    { id: "w_public", text: "SQL join reference", source: "Public SQL reference", kind: "web_snippet", url: "https://example.org/sql", score: 0.8 },
  ], sources: { personal: "ok", web: "ok" }, warnings: [], elapsed_ms: 20, candidate_count: 2 };
}

function payload() {
  const grounding = evidence();
  return { session_id: "grounded", learning_session: {
    summary: "Learn SQL", grounding, review_passed: false, review_notes: "Check source coverage",
    skill_graph: { nodes: [{ id: "joins", label: "SQL joins", description: "Join rows", level: "foundational" }], edges: [] },
    selected_node: "joins", node_state: { joins: { status: "available", best_score: 0, attempts: 0, weaknesses: [], last_feedback: null } },
    lessons: { joins: { content: "# SQL joins\nUse matching rows [source:p_private].", grounding,
      exercise: { format: "case_study", revision: "saved-v1", statement: "Explain a join [source:p_private].", grounding } } },
    chat: [] as { role: string; content: string; grounding?: ReturnType<typeof evidence> }[],
  } };
}

test("path, lesson and practice show honest sources with page/snippet labels on mobile", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mindmorph.userId", "grounding@test.com"));
  await page.route("**/sessions/**", (route) => route.fulfill({ json: payload() }));
  await page.setViewportSize({ width: 375, height: 700 });
  await page.goto("/session/grounded");
  const sources = page.getByRole("region", { name: "Sources", exact: true });
  await expect(sources).toHaveCount(3);
  const toggle = sources.first().locator("summary");
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(sources.first().getByText(/Study notes.pdf.*page 3/)).toBeVisible();
  await expect(sources.first().getByText(/search snippet/)).toBeVisible();
  await expect(sources.first().getByRole("link", { name: "Public SQL reference" })).toHaveAttribute("href", "https://example.org/sql");
  await expect(page.getByText("Use matching rows [1].", { exact: true })).toBeVisible();
  await expect(page.getByText("Check source coverage", { exact: false })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("limited evidence and legacy provenance remain visible without replacing saved practice", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mindmorph.userId", "grounding@test.com"));
  const data = payload();
  data.learning_session.grounding = { ...evidence("unavailable"), passages: [] };
  delete (data.learning_session.lessons.joins as { grounding?: unknown }).grounding;
  delete (data.learning_session.lessons.joins.exercise as { grounding?: unknown }).grounding;
  await page.route("**/sessions/**", (route) => route.fulfill({ json: data }));
  await page.goto("/session/grounded");
  await expect(page.getByText("Supporting sources could not be retrieved. Treat this content as limited evidence.")).toBeVisible();
  await expect(page.getByText("Sources were not recorded for this saved content.")).toHaveCount(2);
  await page.getByLabel("Your response").fill("My saved practice answer");
  await expect(page.getByLabel("Your response")).toHaveValue("My saved practice answer");
});

test("streamed tutor reply retains its source metadata after history refetch", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mindmorph.userId", "grounding@test.com"));
  const data = payload();
  await page.route("**/sessions/**", (route) => {
    if (route.request().url().endsWith("/chat")) {
      data.learning_session.chat = [{ role: "user", content: "Explain joins" },
        { role: "assistant", content: "A join matches rows [source:p_private].", grounding: evidence() }];
      return route.fulfill({ contentType: "text/event-stream", body: [
        { token: "A join matches rows [source:p_private]." }, { grounding: evidence() }, { done: true },
      ].map((frame) => `data: ${JSON.stringify(frame)}\n\n`).join("") });
    }
    return route.fulfill({ json: data });
  });
  await page.goto("/session/grounded");
  await page.getByRole("button", { name: "Ask the tutor" }).click();
  const dialog = page.getByRole("dialog", { name: "Teaching assistant" });
  await dialog.getByLabel("Message the teaching assistant").fill("Explain joins");
  await dialog.getByRole("button", { name: "Send", exact: true }).click();
  await expect(dialog.getByText("A join matches rows [1].", { exact: true })).toBeVisible();
  await dialog.locator("summary").click();
  await expect(dialog.getByText(/Study notes.pdf.*page 3/)).toBeVisible();
});

test("upload explains future generation and does not silently create or regenerate a path", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mindmorph.userId", "grounding@test.com"));
  await page.route("**/sessions/**", (route) => route.fulfill({ json: [] }));
  await page.route("**/users/*/knowledge", (route) => route.fulfill({ json: { filename: "Notes.pdf", chunks: 2 } }));
  const mutations: string[] = [];
  page.on("request", (request) => { if (request.method() === "POST") mutations.push(new URL(request.url()).pathname); });
  await page.goto("/");
  await expect(page.getByText(/Uploads are optional/)).toBeVisible();
  await page.locator("input[type=file]").setInputFiles({ name: "Notes.pdf", mimeType: "application/pdf", buffer: Buffer.from("fixture") });
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Available for future generation" })).toBeVisible();
  expect(mutations).toEqual(["/users/grounding%40test.com/knowledge"]);
});
