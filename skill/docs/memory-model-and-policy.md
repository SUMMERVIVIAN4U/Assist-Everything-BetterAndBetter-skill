# Memory Model And Policy

The memory schema is generic. Do not create scenario-specific memory types.

## Types

- `preference`: soft preference used for ranking or style
- `constraint`: hard limit, taboo, exclusion, or conditional rule
- `workflow`: reusable process or interaction rule
- `decision`: current-task choice that should be continued
- `history`: past action or event that should affect future avoidance or continuity
- `context_fact`: background fact useful for the task

## Scopes

Scopes are domains, not memory types:

- `gift_planning`
- `life_family_travel`
- `study_plan`
- `work_report`
- `research_review`
- `general`

## Validity Layers

- `current_task`: only applies in the current session/task
- `scene_memory`: same scene can recall it, but should confirm before applying
- `long_term`: stable preference or rule, applies by default when scope matches
- `past`: historical fact, mainly for continuity and avoiding repetition

Examples:

- "这次父亲不去" should be `current_task`.
- "家庭旅行常有老人同行，需要确认步行限制" should be `scene_memory`.
- "我不喜欢人挤人的网红点" can be `long_term`.
- "以前送过玫瑰金项链" should be `past`.

## Statuses

- `active`: can be retrieved
- `superseded`: kept for audit, lower priority
- `archived`: retained but not applied by default
- `deleted`: must not be retrieved or applied

## Write Policy

Never silently convert every utterance into long-term memory.

The write path is hybrid:

1. Rule extraction catches high-confidence structured signals such as budget, taboo, previous gifts, explicit deletion, and obvious travel/study/work constraints.
2. LLM semantic extraction handles context-dependent intent such as "选拍立得", "重复候选名就是选定", and user corrections.
3. The skill validates the candidate, assigns confidence, scopes it, dedupes it, and writes only valid memory.

Confidence behavior:

- `reject`: sensitive/private or inappropriate memory
- `ask`: weak signal, needs clarification
- `propose`: medium-confidence long-term memory waits for user approval
- `add`: high-confidence memory can be saved
- `dedupe`: equivalent active memory already exists

Only say memory was saved when the trace contains a real `add` or successful update action.

## Recall Policy

Recall is filtered before ranking:

1. status must be `active`
2. scope must match current task
3. gift recipient/target must match when available
4. deleted or polluted memories are excluded
5. validity layer decides whether memory goes to `apply_now` or `confirm_first`

Ranking uses `retrieval_score` plus time:

- base confidence or Mem0 score
- layer bonus: current task > long term > scene memory > past
- scope match bonus
- keyword/entity hit bonus
- user-approved bonus
- final order: score descending, then update/create time descending

The final LLM answer may default-use `apply_now`. `confirm_first` is for cautious confirmation.

For expired `current_task` memories such as a previous gift budget, the answer should confirm-and-proceed: mention the previous value as a tentative assumption and provide a recommendation under that assumption, instead of asking the user to repeat the value.

## Decay And Conflict

When memory changes:

- contradictory active memory should supersede or downgrade the older memory
- current-task memory should expire at session/task boundary
- scene memory should be recalled as a prompt to confirm, not blindly applied
- deleted memory must stay out of retrieval and final answer

