---
title: Full-Stack Testing & Verification Guidelines
phase: Verification and Quality Assurance (Post-Build Only)
governance: Explicit Approval Required
applicability: General Full-Stack Web Applications
---

# General Full-Stack Testing Guidelines

A pragmatic, production-grade guide for testing modern full-stack web applications (Frontend, Backend, Database, and AI/RAG integrations).

# High-Level Testing & Quality Guidelines

> IMPORTANT NOTICE
> 1. **NOT FOR THE BUILDING PHASE**: This document is strictly intended for post-build verification, regression checks, and quality validation. It must **not** be used during active construction or development phases.
> 2. **USER DIRECTION & APPROVAL MANDATORY**: The user must explicitly direct, review, and approve all testing procedures prior to building.
---



---

## 1. Quality & Verification Philosophy

Testing ensures that a completed full-stack application functions reliably, securely, and seamlessly for end users before deployment or delivery.

- **Outcome-Oriented**: Verify user-facing workflows and system contracts rather than internal wiring.
- **Controlled Scope**: Test only what has been developed and approved for review.
- **No Assumptions**: Flag uncertainties and await user direction before running destructive or extensive test routines.

---

## 2. Core Testing Dimensions

### A. Static & Contract Consistency
- Verify complete type safety and code consistency across both client and server layers.
- Ensure all incoming data is validated against strict schemas before processing.
- Maintain consistent communication contracts between frontend components and backend services.

### B. User Interface & Client Experience
- **Functional Interactions**: Confirm buttons, forms, navigation elements, and interactive panels respond accurately to user actions.
- **Lifecycle States**: Validate that the interface cleanly handles all common operational states:
  - Initial load and ready states
  - In-progress loading indicators
  - Empty data placeholders
  - Graceful error screens and recovery options
- **Accessibility & Responsiveness**: Ensure readable layouts across desktop, tablet, and mobile displays with standard keyboard navigation.

### C. Server & Data Services
- **API Operations**: Check that service routes return appropriate standard response statuses and formatted payloads.
- **Data Integrity**: Ensure database records are created, updated, and removed cleanly without leaving orphan data.
- **Access & Permissions**: Confirm protected resources are strictly accessible only by authorized users.

### D. AI & Intelligent Workflows
- **Output Validation**: Ensure model outputs adhere reliably to requested structured formats without formatting corruption.
- **Routing Reliability**: Verify the system routes user requests to the appropriate search, generation, or processing tools based on user intent.
- **Context Grounding**: Check that AI-generated responses remain grounded in the provided reference materials and cite their sources clearly.
- **Rate Limit & Failure Gracefulness**: Ensure polite fallback messaging when external AI services or quotas are temporarily unavailable.

### E. System Reliability & Security
- **Protective Sanitization**: Confirm user inputs are sanitized against common web injection and scripting vulnerabilities.
- **Credential Safety**: Verify secrets and sensitive environment keys are never exposed to the client browser.
- **Performance Baseline**: Ensure responsive page loads and reasonable response times under standard usage.

---

## 1. Testing Philosophy & Strategy

### The Testing Trophy Model
While the traditional *Testing Pyramid* emphasized massive volumes of isolated unit tests, modern full-stack applications benefit most from the **Testing Trophy** approach:

```
        / \
       / E2E \          -> High confidence, high cost, covers critical business journeys
      /-------\
     / Integr. \        -> Sweet spot: tests components, APIs, and DBs working together
    /-----------\
   /  Unit Tests \      -> Fast: pure utility functions, algorithmic logic, parsers
  /---------------\
 / Static Analysis \    -> Instant: TypeScript compiler, ESLint, schema validators
/-------------------\
```

### Core Tenets
1. **Test behavior, not implementation details**: Refactoring code without changing functionality should not break tests.
2. **Deterministic execution**: Tests must produce identical results across local developer environments and CI runners.
3. **Fail fast, fail loud**: Critical path failures (type errors, database connectivity, auth breaches) should stop the build immediately.
4. **Isolate external side effects**: Mock third-party paid APIs, email delivery, and SMS gateways in automated suites; verify them via scheduled synthetic smoke tests in staging.

---

## 2. Static Analysis & Type Safety (First Line of Defense)

Before running any runtime assertions, enforce compile-time correctness:

- **TypeScript Strict Mode**: Enable `strict: true`, `noImplicitAny: true`, and `strictNullChecks: true`.
- **Linting & Code Quality**: Enforce rules for unused imports, circular dependencies, and common anti-patterns using ESLint / Biome.
- **Contract & Schema Validation**:
  - Validate inbound API payloads and database responses using runtime schema validators (e.g., Zod, Valibot, or Joi).
  - Share TypeScript types/interfaces between frontend and backend to eliminate schema drift.
- **Secret Scanning**: Use automated git hooks (gitleaks, detect-secrets) to ensure API keys and credentials are never committed.

---

## 3. Frontend Testing Guidelines

### A. Component & Hook Testing
- **User-Centric Queries**: Query DOM elements by accessibility role and visible text (`getByRole('button', { name: /submit/i })`) rather than arbitrary CSS classes or test IDs.
- **State & Interaction Scenarios**:
  - Test initial render and default states.
  - Test user events: typing, clicking, form submission, and tab navigation.
  - Test all asynchronous UI states: **Loading Skeleton**, **Success State**, **Empty State**, and **Error Boundary**.
- **Mock Service Worker (MSW)**: Intercept network requests at the browser layer for realistic API mocking without coupling components to specific fetch libraries.

### B. Accessibility (a11y)
- Integrate automated accessibility audits (e.g., `axe-core`, `eslint-plugin-jsx-a11y`).
- Ensure keyboard navigability (Tab, Enter, Escape, Arrow keys) for modals, dropdowns, and menus.
- Verify color contrast ratios (WCAG AA compliance) and appropriate ARIA attributes.

### C. Visual Regression & Responsive Design
- Snapshot key layout viewports (Mobile 375px, Tablet 768px, Desktop 1280px+).
- Use tools like Playwright or Percy to catch unexpected CSS regressions and layout shifts.

---

## 4. Backend & API Testing Guidelines

### A. Endpoint & Contract Testing
- **Status Code Accuracy**:
  - `200 OK` for read/update with body.
  - `201 Created` for successful resource creation with location header.
  - `204 No Content` for deletions.
  - `400 Bad Request` for invalid input schemas.
  - `401 Unauthorized` for missing/expired authentication tokens.
  - `403 Forbidden` for permission/RBAC denials.
  - `404 Not Found` for missing resources.
  - `422 Unprocessable Entity` for semantic domain violations.
  - `429 Too Many Requests` when rate limits are exceeded.
  - `500 Internal Server Error` with sanitized error messages (no stack traces leaked to clients).

### B. Database & Persistence Layer
- **Test Database Isolation**:
  - Run database tests against isolated, ephemeral containerized instances (e.g., Dockerized PostgreSQL / SQLite in-memory).
  - Wrap tests in transactions that roll back upon completion, or truncate tables between test suites.
- **Migration Verification**:
  - Test migration scripts forwards and backwards (up and down) to ensure schema evolutions are safe and non-destructive.
- **Concurrency & Deadlock Prevention**:
  - Simulate concurrent writes to resources requiring atomic operations (e.g., balances, inventory, sequential counters).

### C. Authentication & Authorization Security
- **Role-Based Access Control (RBAC)**: Explicitly test that non-privileged users cannot access admin endpoints.
- **IDOR (Insecure Direct Object Reference)**: Verify User A cannot access or mutate resources owned by User B by changing IDs in query params or route paths.
- **Session Lifecycle**: Verify token expiry, token refresh flows, and complete invalidation upon logout.

---

## 5. End-to-End (E2E) Testing Guidelines

E2E tests simulate actual user journeys across the entire application stack:

### Critical Paths to Cover
1. **Registration & Login**: User sign up -> Email verification simulation -> Login -> Protected Dashboard access.
2. **Core Value Transaction**: Ingesting a source document -> Searching/Querying -> Receiving accurate agent response with citations.
3. **Data Mutation & Deletion**: Creating a resource -> Editing it -> Verifying persistence across page refresh -> Deleting it.
4. **Network Resilience**: Handling offline states, degraded connections, and automated retries.

### E2E Best Practices
- Keep E2E suites concise and targeted; avoid testing every minor permutation in E2E.
- Seed predictable test data via direct backend API endpoints or DB fixtures before each E2E test rather than creating prerequisites through the UI.
- Run tests in parallel across headless browsers (Chromium, Firefox, WebKit).

---

## 6. AI & Multimodal System Testing (Specialized)

For applications incorporating LLMs, embeddings, and Agentic loops:

- **Structured Output Compliance**: Assert that models return valid, parseable JSON conforming to declared schemas (e.g., `responseSchema`).
- **Tool-Calling Verification**: Verify the agent selects the correct tool and parameters given distinct user intents (e.g., routing to `rag_retriever` vs `web_search`).
- **Retrieval Quality Benchmarks (RAG)**:
  - Benchmark chunk recall and context relevance on a curated golden dataset.
  - Validate that dynamic vector metadata filters (e.g., `$in: active_sources`) strictly isolate retrieval results.
- **Citation & Grounding Accuracy**: Verify generated answers include traceable citations referencing the ingested source documents and chunks.
- **Fallback & Rate-Limit Handling**: Test graceful degradation when upstream AI providers return `429` (Quota Exceeded) or `503` (Model Overloaded).

---

## 7. Performance & Load Testing

- **API Latency SLAs**:
  - P50 < 100ms, P95 < 300ms, P99 < 800ms for standard database queries.
  - Measure streaming time-to-first-token (TTFT) for LLM responses.
- **Load & Stress Testing**:
  - Use load testing tools (e.g., k6, Autocannon, Locust) to simulate peak concurrent traffic.
  - Identify bottlenecks: connection pool exhaustion, memory leaks, unindexed database queries (N+1 queries).
- **Frontend Core Web Vitals**:
  - **LCP (Largest Contentful Paint)**: < 2.5s
  - **INP (Interaction to Next Paint)**: < 200ms
  - **CLS (Cumulative Layout Shift)**: < 0.1

---

## 8. Continuous Integration (CI/CD) Pipeline Checklist

A standard automated pull-request validation pipeline:


---

## 9. Quick Testing Matrix Summary

| Layer | Primary Tools | Scope | Frequency |
|---|---|---|---|
| **Static** | TypeScript, ESLint, Prettier | Syntax, types, code style | On every file save / Pre-commit |
| **Unit** | Vitest, Jest | Isolated functions, utilities, math | On every commit |
| **Component** | React Testing Library, MSW | UI interactions, loading/error states | On every PR |
| **API / Backend**| Supertest, Vitest, Testcontainers | Routes, DB transactions, RBAC | On every PR |
| **E2E** | Playwright, Cypress | Critical user journeys, auth | On PR merge / Pre-deploy |
| **Performance** | k6, Lighthouse | Latency, throughput, Web Vitals | Weekly / Pre-release |
| **Security** | Gitleaks, npm audit, Snyk | Secrets, CVEs, dependencies | Daily / On every build |


---

## 3. Testing Execution Process (Post-Build)

When the building phase is complete and the user requests testing:

```
[ Build Phase Complete ]
           │
           ▼
[ Step 1: User Directs Scope ] ────► User defines which features or flows to verify
           │
           ▼
[ Step 2: Testing Plan Review ] ───► Propose verification steps for user confirmation
           │
           ▼
[ Step 3: User Approval ] ─────────► User explicitly authorizes execution
           │
           ▼
[ Step 4: Verification Run ] ──────► Execute tests safely within agreed boundaries
           │
           ▼
[ Step 5: Summary Report ] ────────► Present clear findings, issues, and next steps
```

---

## 4. Summary Checklist

- [ ] Has the user directed which features to verify?
- [ ] Has explicit user approval been given to start testing?
- [ ] Is the building phase complete?
- [ ] Are all UI states (loading, success, error) verified?
- [ ] Are backend service operations responding correctly?
- [ ] Are intelligent AI workflows validated for structured accuracy?
- [ ] Are all credentials and data securely safeguarded?

