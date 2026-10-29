import { type Page, expect, test } from "@playwright/test";

// A 2-node path: 'a' (root, available) → 'b' (locked behind a). Mutated in place across mock calls so
// grading 'a' makes it mastered and 'b' unlocks — exercising the full adaptive loop in the UI.
function makeSession() {
  return {
    skill_graph: {
      summary: "Learn Python",
      nodes: [
        { id: "a", label: "Python Basics", description: "vars", level: "foundational" },
        { id: "b", label: "Data Structures", description: "lists", level: "intermediate" },
      ],
      edges: [{ source: "a", target: "b", relation: "prerequisite" }],
    },
    summary: "Learn Python",
    node_state: {
      a: { status: "available", best_score: 0, attempts: 0, weaknesses: [], last_feedback: null },
      b: { status: "available", best_score: 0, attempts: 0, weaknesses: [], last_feedback: null },
    },
    lessons: {} as Record<string, unknown>,
    selected_node: null as string | null,
    format_type: "B",
  };
}

async function mockApi(
  page: Page,
  opts: {
    injectRemedial?: boolean;
    language?: "python" | "javascript" | "sql";
    harnessError?: boolean;
    // Reviewer verdict as the backend serializes it. `undefined` = key absent from the payload
    // (older sessions); `null` = Reviewer ran but returned no verdict. Both must render nothing.
    review?: { passed?: boolean | null; notes?: string | null };
  } = {},
) {
  const ls = makeSession() as ReturnType<typeof makeSession> & {
    review_passed?: boolean | null;
    review_notes?: string | null;
  };
  const sid = "sess1";
  const { injectRemedial = false, review } = opts;
  if (review) {
    ls.review_passed = review.passed;
    ls.review_notes = review.notes;
  }

  await page.route("**/sessions/*", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    // GET /sessions/{user} → list
    return route.fulfill({ json: [{ session_id: sid, title: "Learn Python", updated_at: null }] });
  });

  await page.route(`**/sessions/*/${sid}`, async (route) => {
    return route.fulfill({ json: { session_id: sid, learning_session: ls } });
  });

  await page.route(`**/sessions/*/${sid}/lessons/a`, async (route) => {
    const language = opts.language ?? "python";
    ls.lessons.a = {
      content: "# Python Basics\nVariables hold values.",
      exercise: {
        format: "coding_challenge",
        language,
        statement: "Write `add(a, b)`.",
        grading_artifact: { format: "coding_challenge", language, unit_tests: [] },
      },
    };
    ls.selected_node = "a";
    return route.fulfill({ json: { session_id: sid, learning_session: ls } });
  });

  await page.route(`**/sessions/*/${sid}/lessons/b`, async (route) => {
    return route.fulfill({ status: 409, json: { detail: { error: "locked", pending: ["Python Basics"] } } });
  });

  await page.route(`**/sessions/*/${sid}/grade**`, async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    if (opts.harnessError) {
      return route.fulfill({ json: {
        session_id: sid,
        learning_session: ls,
        grade_result: { harness_error: true, failures: ["ModuleNotFoundError: test fixture"] },
        new_node_ids: [],
      } });
    }
    ls.node_state.a = { ...ls.node_state.a, status: "mastered", best_score: 100, attempts: 1,
      last_feedback: { score: 100, passed: 1, total: 1 } };
    // Simulate an LLM adaptation inserting a remedial prerequisite node mid-session — only for the
    // test that exercises that adaptation; other tests assert a fixed 2-node total.
    if (injectRemedial && !ls.skill_graph.nodes.some((n) => n.id === "c")) {
      ls.skill_graph.nodes.push({ id: "c", label: "Remedial Topic", description: "gap-fill", level: "foundational" });
      ls.skill_graph.edges.push({ source: "c", target: "b", relation: "prerequisite" });
      ls.node_state.c = { status: "available", best_score: 0, attempts: 0, weaknesses: [], last_feedback: null };
    }
    return route.fulfill({ json: {
      session_id: sid,
      learning_session: ls,
      grade_result: { score: 100, passed: 1, total: 1, feedback: "All checks passed." },
      new_node_ids: injectRemedial ? ["c"] : [],
    } });
  });

  await page.route("**/sessions/followup", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    return route.fulfill({ json: {
      questions: [
        { id: "scope", question: "How much depth do you want?", options: ["Foundation", "Advanced"], default: 0 },
        { id: "pace", question: "What pace works for you?", options: ["Steady", "Intensive"], default: 0 },
      ],
      detected_domain: "programming",
      detected_language: "python",
      detected_specialization: null,
    } });
  });

  // Registered last so it wins over the broader "**/sessions/*" list mock above (Playwright matches
  // routes in reverse registration order — last registered is tried first).
  await page.route("**/sessions/stream", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    const frames = [
      { stage: "scout", label: "Scouting sources" },
      { stage: "academic", label: "Consulting academic agent" },
      { done: true, session: { route: "SCOUT", session_id: sid, learning_session: ls } },
    ];
    const body = frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join("");
    return route.fulfill({ contentType: "text/event-stream", body });
  });
}

test("full loop: login → graph → lesson → grade → mastery", async ({ page }) => {
  await mockApi(page);

  await page.goto("/");
  await page.getByLabel("Email").fill("e2e@test.com");
  await page.getByRole("button", { name: "Enter" }).click();

  // Dashboard → start a path
  await page.getByLabel("What do you want to learn?").fill("Learn Python");
  await page.getByRole("button", { name: "Generate" }).click();

  // Lands on the session page. This is a client-side router.push into /session/[id], and the dev
  // server compiles that route on demand the first time any test reaches it — measured at ~12s cold
  // vs ~0.5s warm. The other tests enter via page.goto(), whose 30s navigation timeout absorbs that;
  // this one has to wait for the transition explicitly, or the 5s expect() default fires mid-compile.
  await page.getByRole("heading", { name: "How much depth do you want?" }).waitFor();
  await page.getByRole("button", { name: "Next →" }).click();
  await page.getByRole("heading", { name: "What pace works for you?" }).waitFor();
  await page.getByRole("button", { name: /Generate Path/ }).click();
  await expect(page.getByRole("heading", { name: "Your Learning Path is Ready!" })).toBeVisible();
  await page.getByRole("button", { name: /Explore Your Path/ }).click();
  await page.waitForURL("**/session/**");
  await expect(page.getByText("skills complete")).toBeVisible();
  await expect(page.getByText("/2")).toBeVisible();

  // Open the root node's lesson.
  await page.getByText("Python Basics").first().click();
  // The lesson leads with the markdown's own h1 (the panel no longer renders a duplicate title).
  await expect(page.getByRole("heading", { name: "Python Basics", level: 1 })).toBeVisible();
  await expect(page.getByText("Variables hold values.")).toBeVisible();

  // Grade a (mocked perfect score) → node becomes complete, counter ticks to 1/2.
  // Monaco's real input is .inputarea; click the editor then type so onChange enables the button.
  await page.locator(".monaco-editor").first().click();
  await page.keyboard.type("def add(a,b): return a+b");
  await page.getByRole("button", { name: "Grade my submission" }).click();
  await expect(page.getByText("100%", { exact: true })).toBeVisible();
  await expect(page.getByText("1/2")).toBeVisible();
  await expect(page.getByText("All checks passed.")).toBeVisible();
});

for (const language of ["javascript", "sql"] as const) {
  test(`${language} coding exercise opens the named editor and shows its grade result`, async ({ page }) => {
    await mockApi(page, { language });
    await openSession(page);
    await page.getByText("Python Basics").first().click();
    await expect(page.locator(".monaco-editor")).toBeVisible();
    const languageIds = await page.evaluate(() => {
      const monaco = (window as Window & {
        monaco?: { editor?: { getModels: () => Array<{ getLanguageId: () => string }> } };
      }).monaco;
      return monaco?.editor?.getModels().map((model) => model.getLanguageId()) ?? [];
    });
    expect(languageIds).toContain(language);
    await page.locator(".monaco-editor").first().click();
    await page.keyboard.type(language === "sql" ? "SELECT 1;" : "function add(a,b) { return a+b; }");
    await page.getByRole("button", { name: "Grade my submission" }).click();
    await expect(page.getByText("All checks passed.")).toBeVisible();
    await expect(page.getByText("100%", { exact: true })).toBeVisible();
  });
}

test("grading harness failure is explained without showing a zero or changing mastery", async ({ page }) => {
  await mockApi(page, { harnessError: true });
  await openSession(page);
  await page.getByText("Python Basics").first().click();
  await page.locator(".monaco-editor").first().click();
  await page.keyboard.type("def add(a,b): return a+b");
  await page.getByRole("button", { name: "Grade my submission" }).click();
  await expect(page.getByText("Not graded — the exercise's auto-grading tests are broken on our side.")).toBeVisible();
  await expect(page.getByText(/Your submission was not scored and your progress is unchanged/)).toBeVisible();
  await expect(page.getByText("ModuleNotFoundError: test fixture")).toBeVisible();
  await expect(page.getByText("0%", { exact: true })).toHaveCount(0);
  await expect(page.getByText("skills complete")).toBeVisible();
  await expect(page.getByText("0/2")).toBeVisible();
});

test("remedial node added mid-session gets the entrance animation, even under StrictMode double-render", async ({
  page,
}) => {
  await mockApi(page, { injectRemedial: true });
  const sid = "sess1";

  // Navigate straight to the session route (bypassing the dashboard's session-creation flow, which
  // is a different code path owned elsewhere) — this exercises SkillGraph exactly as it mounts and
  // re-renders under React 19 StrictMode (next.config.mjs: reactStrictMode: true).
  await page.addInitScript(() => localStorage.setItem("mindmorph.userId", "e2e@test.com"));
  await page.goto(`/session/${sid}`);
  await expect(page.getByText("skills complete")).toBeVisible();

  // Nodes present on first paint must NOT be flagged "new" once the graph settles — this is the
  // regression this test guards: a useMemo factory mutating a ref during render gets double-invoked
  // by StrictMode in dev, which previously made the entrance class never apply to anything, including
  // genuinely new nodes added later.
  await page.getByText("Python Basics").first().click();
  await expect(page.getByRole("heading", { name: "Python Basics", level: 1 })).toBeVisible();
  await expect(page.locator(".surface.skill-node-enter")).toHaveCount(0);

  // Grading 'a' also injects a remedial node 'c' into the skill graph (simulating an LLM adaptation
  // after a sub-40 grade) — exercising the exact path the entrance animation exists for.
  await page.locator(".monaco-editor").first().click();
  await page.keyboard.type("def add(a,b): return a+b");
  await page.getByRole("button", { name: "Grade my submission" }).click();
  await expect(page.getByText("100%", { exact: true })).toBeVisible();

  // The new node gets the entrance class; the two nodes seen since first paint do not.
  await expect(page.locator(".surface.skill-node-enter", { hasText: "Remedial Topic" })).toHaveCount(1);
  await expect(
    page.locator(".react-flow__node", { hasText: "Python Basics" }).locator(".skill-node-enter"),
  ).toHaveCount(0);
  await expect(
    page.locator(".react-flow__node", { hasText: "Data Structures" }).locator(".skill-node-enter"),
  ).toHaveCount(0);
});

// React Flow's own node wrapper used to own focus, and its Enter/Space handler only mutated the
// library's internal selection — it never reached onNodeClick, so a keyboard-only user got nothing.
// The card now owns tabIndex/role/aria-label/keydown itself; these guard that path.
async function openSession(page: Page) {
  await page.addInitScript(() => localStorage.setItem("mindmorph.userId", "e2e@test.com"));
  await page.goto("/session/sess1");
  await expect(page.getByText("skills complete")).toBeVisible();
}

for (const key of ["Enter", "Space"]) {
  test(`keyboard ${key} on an available node opens its lesson`, async ({ page }) => {
    await mockApi(page);
    await openSession(page);

    await page.getByRole("button", { name: "Start Here. Python Basics. Available" }).focus();
    await page.keyboard.press(key);

    // Same observable outcome the mouse test asserts, so the two paths are proven equivalent.
    await expect(page.getByRole("heading", { name: "Python Basics", level: 1 })).toBeVisible();
    await expect(page.getByText("Variables hold values.")).toBeVisible();
  });
}

test("keyboard activation of a locked node surfaces the lock message", async ({ page }) => {
  await mockApi(page);
  await openSession(page);

  await page.getByRole("button", { name: /^Data Structures\./ }).focus();
  await page.keyboard.press("Enter");

  await expect(page.getByText(/Locked — first complete: Python Basics/)).toBeVisible();
});

test("skill cards expose status (and the unmet prerequisite) in their accessible name", async ({
  page,
}) => {
  await mockApi(page);
  await openSession(page);

  await expect(page.getByRole("button", { name: "Python Basics. Available" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Data Structures. Locked. Requires: Python Basics" }),
  ).toBeVisible();
});

test("locked node shows a lock message", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByLabel("Email").fill("e2e@test.com");
  await page.getByRole("button", { name: "Enter" }).click();
  await page.getByLabel("What do you want to learn?").fill("Learn Python");
  await page.getByRole("button", { name: "Generate" }).click();
  // Same cold-compile wait as the full-loop test — see the comment there.
  await page.getByRole("heading", { name: "How much depth do you want?" }).waitFor();
  await page.getByRole("button", { name: "Next →" }).click();
  await page.getByRole("button", { name: /Generate Path/ }).click();
  await expect(page.getByRole("heading", { name: "Your Learning Path is Ready!" })).toBeVisible();
  await page.getByRole("button", { name: /Explore Your Path/ }).click();
  await page.waitForURL("**/session/**");
  await expect(page.getByText("skills complete")).toBeVisible();

  // 'Data Structures' is locked behind 'Python Basics'.
  await page.getByText("Data Structures").first().click();
  await expect(page.getByText(/Locked — first complete: Python Basics/)).toBeVisible();
});

// The Reviewer agent can reject a generated graph while the pipeline serves it anyway. The learner
// gets an editor's-note caveat carrying the actual reason — but ONLY on an explicit `false`.
test("a rejected review surfaces a dismissible caveat with the reviewer's reason", async ({ page }) => {
  await mockApi(page, {
    review: {
      passed: false,
      // Model-generated text, so it's untrusted: the markup here must survive as literal
      // characters. Guards against anyone swapping the JSX interpolation for react-markdown
      // (already a dependency, used by LessonPanel next door) and reopening the injection hole.
      notes:
        'a critical foundational skill—core Machine Learning concepts—is <b>absent</b> <img src=x onerror="alert(1)">.',
    },
  });
  await openSession(page);

  const note = page.getByRole("complementary", { name: "Note about this path" });
  await expect(note).toBeVisible();
  await expect(note).toContainText("core Machine Learning concepts—is <b>absent</b>");
  await expect(note.locator("b, img")).toHaveCount(0);
  // An editor's note, not an error: it must not claim the alert role. Scoped to `main` — the
  // Next.js dev-tools overlay parks its own role="alert" element outside the app's markup.
  await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);

  await note.getByRole("button", { name: /Dismiss/ }).click();
  await expect(note).toHaveCount(0);
});

test("a rejected review with no reason still explains itself", async ({ page }) => {
  await mockApi(page, { review: { passed: false, notes: "   " } });
  await openSession(page);

  const note = page.getByRole("complementary", { name: "Note about this path" });
  await expect(note).toBeVisible();
  await expect(note).toContainText("flagged a possible gap in its coverage");
});

// The regression that matters most: absence of a verdict must never read as failure.
for (const [name, review] of [
  ["passing", { passed: true, notes: null }],
  ["null (Reviewer returned no verdict)", { passed: null, notes: null }],
  ["absent from the payload", { passed: undefined, notes: undefined }],
] as const) {
  test(`no caveat when the review verdict is ${name}`, async ({ page }) => {
    await mockApi(page, { review });
    // Awaits the session actually rendering, so the absence assertions can't pass vacuously.
    await openSession(page);

    await expect(page.getByRole("complementary", { name: "Note about this path" })).toHaveCount(0);
    await expect(page.getByText(/Reviewer’s note/)).toHaveCount(0);
  });
}
