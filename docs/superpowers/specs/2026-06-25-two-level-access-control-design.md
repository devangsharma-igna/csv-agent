# Two-Level Access Control Design

## Objective

Add a simple login system with exactly two hardcoded accounts:

| Role | Username | Password |
|---|---|---|
| Super Admin | `igna.admin@gmail.com` | `admin@123` |
| User | `igna.user@gmail.com` | `user@123` |

There is no signup, password reset, JWT, or persistent user database. A login lasts until explicit logout or browser close.

## Authorization Rules

The backend is the source of truth for every permission decision.

- Both roles may list tables, inspect cached context, ask natural-language read questions, and view returned raw rows.
- A User may not preview or import CSV files, refresh stored context, create pending writes, confirm writes, or perform any DML/DDL operation.
- A Super Admin may perform all existing operations, including DML and destructive DDL such as `DROP TABLE`, through the existing preview-and-confirm flow.
- Raw SQL submitted as chat input is rejected for both roles before the request reaches the query planner, LLM, pending-write store, or database.
- Frontend visibility is only a usability measure. Hiding an admin control never replaces a backend authorization check.

## Authentication Architecture

Add a small backend authentication module containing:

- the two fixed credential records and their roles;
- an in-memory set or map of opaque session identifiers;
- secure random session creation;
- current-user lookup from an HTTP-only cookie;
- reusable FastAPI dependencies for `authenticated user` and `Super Admin`.

Successful login sets an HTTP-only, `SameSite=Strict` session cookie without `Max-Age` or `Expires`, making it a browser-session cookie. Logout removes the server-side session and clears the cookie. Unknown, missing, or expired-process sessions return HTTP 401. Authenticated but unauthorized requests return HTTP 403.

The cookie is marked `Secure` when configured for HTTPS and remains non-secure for the current localhost HTTP development environment.

Backend restart invalidates all sessions. This is acceptable because sessions are intentionally in-memory and non-persistent.

## API Changes

Add:

- `POST /api/auth/login` - validates the fixed credentials, creates a session, and returns the username and role.
- `GET /api/auth/me` - restores the current frontend session state.
- `POST /api/auth/logout` - invalidates the session and clears the cookie.

Leave `GET /api/health` public. Require authentication for all other application endpoints.

Apply Super Admin authorization directly to every write-capable entry point:

- CSV preview and commit endpoints;
- context refresh;
- creation of a pending mutating query;
- confirmation of a pending write;
- cancellation of a pending write.

Read endpoints remain available to both roles.

The query endpoint will still use the existing SQL classifier as a defense-in-depth check. If a User's natural-language request unexpectedly produces mutating SQL, the backend returns HTTP 403 and does not create a pending write. A Super Admin receives the existing confirmation response.

Pending writes record the creating Super Admin's session identity. Confirmation and cancellation require the same authenticated Super Admin session so one session cannot consume another session's pending operation.

## Raw SQL Guardrail

Before invoking the query planner, classify the chat question as either natural language or raw SQL. Reject inputs that structurally resemble executable SQL, including:

- leading SQL verbs such as `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `GRANT`, `REVOKE`, `WITH`, `CALL`, `EXECUTE`, or `MERGE`;
- common SQL comment or multi-statement syntax such as `--`, `/* ... */`, or statement separators used with SQL commands;
- code-fenced SQL;
- common injection-shaped Boolean or union expressions.

The response is HTTP 400 with a stable `raw_sql_denied` error. The guardrail should avoid rejecting ordinary natural-language questions merely because they contain words such as "select," "update," or "table."

This is an application policy guardrail, not the sole database defense. Role authorization and the existing mutating-SQL classifier remain mandatory.

## Frontend Design

Add a `/login` page with username and password fields and no signup link. On application startup, call `/api/auth/me` and render protected routes only after the session check completes.

The application header shows the signed-in username, role, and a Logout button.

- Both roles see the Query page.
- Only Super Admin sees the Upload CSV navigation and route.
- User attempts to navigate directly to `/upload` are redirected to the Query page.
- Write confirmation controls are rendered only for Super Admin, while backend checks remain authoritative.
- HTTP 401 responses clear frontend auth state and redirect to login.
- HTTP 403 responses display the backend denial without logging the user out.

## Raw Data Disclosure

Successful read responses include the existing bounded result rows in the API response. The Query page adds a collapsed `Raw data` disclosure next to the existing `SQL` disclosure.

When expanded:

- render rows as a table with stable column headers;
- place the table inside a bounded-height container;
- use vertical scrolling for many rows and horizontal scrolling for wide tables;
- keep headers visually distinct and optionally sticky;
- show a clear empty-state message when there are no rows;
- preserve compact spacing so the disclosure does not distort the chat layout.

No additional database query is issued when the disclosure opens. It displays only rows already returned by the existing read execution.

## Surgical Change Boundaries

The implementation will preserve the current query planner, context builder, database adapter, SQL confirmation workflow, CSV inference, and table replacement logic.

Changes will be limited to:

- a focused authentication/authorization module and auth router;
- small dependency additions to existing route functions;
- a raw-SQL input guard;
- adding read rows to the existing query response;
- frontend auth state, login/protected routing, role-based navigation, and raw-data rendering;
- targeted tests and documentation.

No database schema, external identity provider, JWT library, or broad router refactor will be introduced.

## Error Handling and Logging

- Login failure returns HTTP 401 with a generic invalid-credentials message.
- Missing authentication returns HTTP 401.
- Insufficient role returns HTTP 403.
- Raw SQL chat input returns HTTP 400 with `raw_sql_denied`.
- Logs may include username, role, route, and authorization outcome, but never passwords or session identifiers.
- Existing database and agent error behavior remains unchanged.

## Testing Strategy

Backend tests will verify:

- valid login for each fixed account and rejection of invalid credentials;
- session restoration, logout, and browser-session cookie attributes;
- unauthenticated access is rejected;
- User read access succeeds;
- User write, upload, refresh, confirmation, and DDL paths are rejected without database execution;
- Super Admin write and DDL requests still require confirmation;
- only the creating Super Admin session may confirm or cancel its pending write;
- raw SQL and representative injection-shaped chat inputs are rejected for both roles before planner execution;
- ordinary natural-language questions containing SQL-adjacent words are not falsely rejected;
- successful read responses expose bounded raw rows.

Frontend verification will cover:

- login/session restoration and logout;
- role-based navigation and protected routes;
- User denial handling;
- Super Admin confirmation controls;
- raw-data disclosure behavior for empty, tall, and wide result sets;
- existing TypeScript production build.

## Acceptance Criteria

1. Unauthenticated users see only the login page.
2. The two fixed accounts authenticate with their specified credentials and receive the correct role.
3. A User can perform existing read flows but cannot trigger any database or application write path.
4. A Super Admin retains every existing operation, including destructive DDL, with confirmation.
5. Raw SQL chat input is denied for both roles before LLM or database execution.
6. Read results can be inspected through a well-contained, scrollable Raw data disclosure.
7. Direct API calls cannot bypass frontend role restrictions.
8. Existing query, upload, and confirmation business logic remains otherwise unchanged.
