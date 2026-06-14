# Course catalog

This catalog lists every track FUNiX offers and the courses within each track, in the
recommended order. Each course entry states its tier, its prerequisites, and the skills a
graduate is expected to demonstrate. Use the section that matches the student's goal; do
not read the whole catalog when one track answers the question.

## Programming foundations
The entry track for students with no prior coding experience. It builds the habits the
later tracks assume: reading errors carefully, decomposing a problem, and testing as you
go. Courses, in order:
- **PF101 Introduction to programming** (Foundation tier). No prerequisites. Variables,
  control flow, functions, and basic data structures in Python. Graduates can write a
  small command-line program from a written specification.
- **PF102 Problem solving with data structures** (Foundation tier). Requires PF101. Lists,
  maps, sets, stacks, queues, and when to reach for each. Graduates can choose an
  appropriate structure for a stated access pattern and justify the choice.
- **PF103 Version control and collaboration** (Foundation tier). Requires PF101. Git, pull
  requests, code review etiquette, and resolving merge conflicts. Graduates can contribute
  to a shared repository without clobbering a teammate's work.

## Web development
For students who want to build and ship browser applications. Assumes the foundations
track or equivalent experience. Courses, in order:
- **WD201 HTML, CSS, and the box model** (Foundation tier). Requires PF101. Semantic
  markup, layout, and responsive design. Graduates can build an accessible static page
  from a design mock.
- **WD202 JavaScript and the DOM** (Professional tier). Requires WD201. Events, fetch,
  and state in the browser. Graduates can build an interactive page that talks to an API.
- **WD203 Front-end frameworks** (Professional tier). Requires WD202. Component models,
  routing, and client state. Graduates can structure a multi-screen single-page app.
- **WD204 Back-end services** (Professional tier). Requires WD202. HTTP servers, routing,
  persistence, and authentication. Graduates can stand up a CRUD API with a database.

## Data and analytics
For students aiming at data analysis and reporting roles. Courses, in order:
- **DA301 Data wrangling** (Professional tier). Requires PF102. Cleaning, joining, and
  reshaping tabular data. Graduates can turn a messy export into an analysis-ready table.
- **DA302 Statistics for analysts** (Professional tier). Requires DA301. Distributions,
  hypothesis testing, and confidence intervals. Graduates can state what a result does
  and does not support.
- **DA303 Dashboards and storytelling** (Professional tier). Requires DA301. Visual
  encoding, dashboard design, and narrative. Graduates can build a dashboard a
  non-technical stakeholder can read unaided.

## Machine learning
For students with a data background who want to build predictive systems. Courses:
- **ML401 Foundations of machine learning** (Specialization tier). Requires DA302.
  Supervised learning, train/validation/test discipline, and overfitting. Graduates can
  train and honestly evaluate a baseline model.
- **ML402 Deep learning** (Specialization tier). Requires ML401. Neural networks,
  backpropagation, and the major architectures. Graduates can train a small network on a
  labelled dataset and read a training curve.
- **ML403 Applied ML systems** (Specialization tier). Requires ML401. Feature pipelines,
  serving, and monitoring drift in production. Graduates can ship a model behind an API
  and detect when it degrades.

## Cloud and operations
For students moving toward platform and reliability roles. Courses, in order:
- **CO501 Linux and the command line** (Foundation tier). No prerequisites. Files,
  processes, permissions, and shell scripting. Graduates are comfortable operating a
  remote server over SSH.
- **CO502 Containers and orchestration** (Professional tier). Requires CO501 and WD204.
  Images, registries, and scheduling. Graduates can containerize a service and run it in
  a cluster.
- **CO503 Reliability engineering** (Specialization tier). Requires CO502. Monitoring,
  alerting, incident response, and postmortems. Graduates can define service objectives
  and run a blameless incident review.

## Security
For students specializing in application and infrastructure security. Courses:
- **SE601 Security fundamentals** (Professional tier). Requires WD204. The common
  vulnerability classes and the principle of least privilege. Graduates can spot and fix
  the most common web vulnerabilities.
- **SE602 Offensive security** (Specialization tier). Requires SE601. Threat modelling and
  authorized penetration testing. Graduates can run a structured assessment of a web app.

## Mobile development
For students who want to build native and cross-platform mobile apps. Courses, in order:
- **MD701 Mobile UI fundamentals** (Professional tier). Requires WD201. Screens,
  navigation, and platform conventions on iOS and Android. Graduates can build a
  multi-screen app shell that feels native on both platforms.
- **MD702 State and data on device** (Professional tier). Requires MD701 and WD202. Local
  storage, offline-first sync, and background tasks. Graduates can build an app that works
  without a connection and reconciles when it returns.
- **MD703 Publishing and telemetry** (Specialization tier). Requires MD702. Store
  submission, crash reporting, and usage analytics. Graduates can ship to both stores and
  read the post-launch health of a release.

## Databases
For students who want to design and operate data stores. Courses, in order:
- **DB801 Relational modelling** (Professional tier). Requires PF102. Normalization, keys,
  indexes, and query planning. Graduates can design a schema that serves a stated set of
  queries efficiently.
- **DB802 Transactions and concurrency** (Specialization tier). Requires DB801. Isolation
  levels, locking, and the trade-offs each makes. Graduates can reason about anomalies a
  given isolation level does and does not prevent.
- **DB803 Scaling data stores** (Specialization tier). Requires DB802 and CO502.
  Replication, partitioning, and caching. Graduates can choose a scaling strategy that
  matches a workload's read/write shape.

## Quality and testing
For students specializing in test engineering and quality. Courses, in order:
- **QA901 Testing fundamentals** (Professional tier). Requires PF103. Unit, integration,
  and end-to-end testing, and what each is good for. Graduates can build a layered test
  suite that runs in CI.
- **QA902 Test automation** (Specialization tier). Requires QA901 and WD202. Page objects,
  fixtures, and flaky-test triage. Graduates can build and maintain a stable automated
  browser suite.

## Capstone
Every program ends with a capstone. The student proposes a project, an advisor approves
the scope, and the student delivers it over a full semester with weekly check-ins. The
capstone tier matches the student's program tier. Graduates leave with one substantial,
defensible project in their portfolio. The capstone is graded on the working artifact, a
written design rationale, and a live defense in front of two advisors.
