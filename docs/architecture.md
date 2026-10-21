# Croma agentic profit planning and pricing: end-state flow

End state on Microsoft Foundry plus Databricks. The data layer is pluggable: the hub's data tool hands questions to a backend (a Databricks Genie space, your own function, or a stub), see `integrations/genie/README.md`. The diagram below shows the full target picture, including parts that are later phases.

Solid lines are in scope. Dashed lines and boxes are later phases or still to be designed.

## 1. End-state architecture

```mermaid
%%{init: {"flowchart": {"wrappingWidth": 340, "nodeSpacing": 40, "rankSpacing": 50}}}%%
flowchart TB
    classDef user fill:#e8f1fb,stroke:#2b6cb0,color:#1a365d
    classDef pre fill:#e6fffa,stroke:#2c7a7b,color:#1d4044
    classDef data fill:#fffaf0,stroke:#c05621,color:#5f370e,stroke-width:2px
    classDef post fill:#f0fff4,stroke:#2f855a,color:#1c4532
    classDef found fill:#f7fafc,stroke:#718096,color:#2d3748
    classDef dbx fill:#fff5f5,stroke:#c53030,color:#63171b
    classDef gate fill:#faf5ff,stroke:#6b46c1,color:#322659,stroke-width:2px
    classDef later stroke-dasharray: 5 5,fill:#fdfdfd,stroke:#a0aec0,color:#4a5568

    subgraph EXP["Experience layer"]
        U(["Business user<br/>Entra ID sign-in"])
        TEAMS["Microsoft Teams<br/>chat, adaptive cards"]
        WEB["Web app"]
        SCHED["Scheduler / nudges<br/>Logic Apps or Functions"]:::later
    end

    subgraph FGATE["Front gateway: Azure API Management"]
        FGW["API gateway<br/>validates the Entra ID token, throttles per user and channel<br/>quotas, traffic monitoring, request logging, routing"]
    end

    subgraph FOUNDRY["Microsoft Foundry: Agent Service"]
        direction TB

        subgraph PRE["PRE AGENT: conversation front end"]
            CX["<b>CX COMMUNICATION AGENT</b><br/><i>Pre: front of the system</i><br/>• greets and identifies the user<br/>• asks for missing details: scope, period, KPI<br/>• validates and confirms the request<br/>• routes to the right post agent"]
        end

        CASE[("Shared case context<br/>request, findings so far, data pulled, open questions<br/>thread state and memory")]

        subgraph POST["POST AGENTS: no fixed order, each calls the Data agent as often as it needs"]
            direction LR
            RES["<b>RESEARCH AGENT</b><br/><i>Post: investigates</i><br/>• researches a topic or anomaly<br/>• asks the Data agent, reads,<br/>asks again as leads appear"]
            ANA["<b>ANALYSIS AGENT</b><br/><i>Post: analyses</i><br/>• analyses a business KPI<br/>• drivers, trends, variance"]
            REP["<b>REPORTING AGENT: editor</b><br/><i>Post: reports</i><br/>• runs the report team<br/>• audits and publishes<br/>• designed DOCX, prescriptive"]
            LKP["<b>LOOKUP AGENT</b><br/><i>Post: answers</i><br/>• plain 'talk to my data' questions<br/>• one question, one figure<br/>• escalates if it needs explaining"]
            LKP ~~~ RES ~~~ ANA ~~~ REP
        end

        subgraph TEAM["REPORT TEAM: deep-reasoning model, high reasoning budget"]
            direction LR
            PLN["<b>PLANNER</b><br/>• report type and depth<br/>• sections and data requests"]
            INS["<b>INSIGHT ANALYST</b><br/>• reconciles, decomposes<br/>• tests hypotheses<br/>• ranked insight cards"]
            STR["<b>STRATEGIST</b><br/>• sized, owned actions<br/>• guardrails and KPIs"]
            WRT["<b>WRITER</b><br/>• frame and sections<br/>• tables and charts by reference"]
            REV["<b>REVIEWER</b><br/>• independent critique<br/>• spot-checks the numbers"]
            PLN ~~~ INS ~~~ STR ~~~ WRT ~~~ REV
        end

        STUDIO[("Report studio<br/>evidence ledger: every result and calculation has an id<br/>number audit: each figure in the text traced to the ledger<br/>renderer: DOCX and HTML, charts, evidence appendix")]

        subgraph DATAAG["DATA AGENT: the only agent that touches data"]
            DA["<b>DATA AGENT</b><br/><i>Data: tool user of Genie</i><br/>• turns a question into a data request<br/>• checks the result is sensible<br/>• returns the data with its source"]
        end

        subgraph FOUND["Foundations used by every agent"]
            direction TB
            MODEL["Model deployments<br/>GPT family"]
            IDN["Entra Agent ID<br/>RBAC, on-behalf-of user"]
            SAFE["Guardrails<br/>content safety, PII, prompt shields"]
            OBS["Tracing and monitoring<br/>Application Insights"]
            EVAL["Evaluations"]:::later
        end
    end

    subgraph GATE["Data tool gateway: Azure API Management"]
        direction LR
        LIM["Controls<br/>per-user limits, max query size and cost, audit"]
        CACHE["Result cache<br/>to be designed"]:::later
    end

    subgraph DBX["Databricks"]
        direction TB
        MCP["Genie MCP server"]
        GENIE["Genie space, trimmed<br/>natural language to SQL only<br/>on curated tables, with instructions and sample queries"]
        WH["SQL warehouse"]
        subgraph UC["Unity Catalog: governance, lineage, row and column security"]
            GOLD[("Gold data products<br/>sales, stock, price, cost,<br/>schemes, margin rules, events, offers")]
            PLAN[("Pricing outputs<br/>model results, recommendations, plans")]
        end
        JOBS["Lakeflow jobs and ML<br/>demand model, optimiser, rules"]
    end

    subgraph SRC["Source systems"]
        SAP["SAP and HANA"]
        OTHER["CRM, OMS, WMS, events"]
    end

    OUT[("Report store<br/>SharePoint or Blob")]

    U --> TEAMS
    U --> WEB
    SCHED -.-> FGW
    TEAMS <--> FGW
    WEB <--> FGW
    FGW <--> CX

    CX -->|"hands off a confirmed request"| POST
    CX <-.-> CASE
    POST <-.->|"findings in, findings out"| CASE
    POST <-->|"ask and answer, as often as needed"| DA
    REP -->|"directs"| TEAM
    TEAM <-->|"reads and writes evidence, insights, actions, document"| STUDIO
    TEAM -.->|"follow-up questions"| DA

    STUDIO -->|"audited DOCX with charts"| OUT
    OUT -.->|"link or card back to the user"| CX

    DA -->|"tool call"| LIM
    LIM --> MCP
    LIM -.-> CACHE
    MCP --> GENIE
    GENIE --> WH
    WH --> UC

    SAP --> GOLD
    OTHER --> GOLD
    GOLD --> JOBS
    JOBS --> PLAN

    class U,TEAMS,WEB user
    class FGW,LIM gate
    class CX pre
    class DA data
    class LKP,RES,ANA,REP,PLN,INS,STR,WRT,REV post
    class STUDIO found
    class MODEL,IDN,SAFE,OBS,CASE found
    class MCP,GENIE,WH,GOLD,PLAN,JOBS dbx
```

### How to read it

| Box | What it does | What it deliberately does not do |
|---|---|---|
| **CX agent** | Owns the conversation. Collects and confirms the right details before any analysis runs, then routes. It can also open a conversation from a nudge or a schedule later. | Query data or analyse anything itself. |
| **Data agent** | The single path to data. Frames the question for Genie, calls it as a tool when needed, sanity-checks the answer and returns it with its source. | Analyse, recommend or write reports. |
| **Lookup agent** | The fast path for plain questions such as "how many sales in March". No case, no confirmation round: it states the interpretation it used, asks the Data agent once and returns the figure. If the question needs explaining, it hands back to CX to route to Analysis. | Analyse, explain or compare drivers. |
| **Research and Analysis agents** | Do the thinking. Each decides at run time what data it needs and asks the Data agent, as many times as it takes. | Reach data except through the Data agent. |
| **Reporting agent (editor) and the report team** | The editor runs a small team on a deep-reasoning model: Planner (what the report should say and what evidence it needs), Insight analyst (reconcile, decompose, test explanations), Strategist (sized, owned actions), Writer (the document), Reviewer (independent critique). Each reads and writes a shared workspace; the editor decides when it is good enough and publishes. | Write a number that is not in the evidence ledger. |
| **Report studio** | Holds the evidence ledger (every data result and every calculation has an id), audits that each figure in the text traces to it, and renders the DOCX and HTML with charts and an evidence appendix. | Hold business logic or judgement. |
| **Shared case context** | Keeps the request, the findings so far and the data pulled, so Reporting can write from what Research and Analysis already established. | Store raw data extracts longer than needed. |
| **Front gateway** | The only way in for every channel, including scheduled nudges. Validates the Entra ID token, throttles and sets quotas per user and channel, monitors traffic and logs requests. | Hold conversation or business logic. |
| **Data tool gateway** | One controlled door to Databricks: per-user limits, a maximum query size or cost, audit, and later a result cache. | Hold business logic. |
| **Genie (trimmed)** | Natural language to SQL over curated Unity Catalog tables. | Run the pricing model, optimiser or rules. Those are Lakeflow jobs whose results land back in Unity Catalog as tables. |
| **Unity Catalog** | Governs the data: lineage, and row and column security applied for the signed-in user. | |

## 2. How a request flows

There is no fixed pipeline: after hand-off the post agents choose their own next step. This is one typical run.

```mermaid
sequenceDiagram
    autonumber
    actor U as Business user
    participant CX as CX agent
    participant CTX as Case context
    participant AN as Analysis agent
    participant RS as Research agent
    participant DA as Data agent
    participant G as Genie via gateway
    participant RP as Reporting agent

    U->>CX: "Why did accessories margin slip in West?"
    CX->>U: Which period, stores and margin definition?
    U->>CX: Sept, West zone, net margin after schemes
    CX->>CTX: Save the confirmed request
    CX->>AN: Hand off the confirmed request

    loop Analysis asks for what it needs
        AN->>DA: Margin by brand and week, West, Sept
        DA->>G: Natural language question
        G-->>DA: Rows and the SQL used
        DA-->>AN: Data, source, caveats
        AN->>CTX: Add finding
    end

    AN->>RS: Brand X looks like the driver, find out why
    loop Research follows the lead
        RS->>DA: Cost and scheme terms for brand X
        DA->>G: Natural language question
        G-->>DA: Rows and the SQL used
        DA-->>RS: Data, source, caveats
        RS->>CTX: Add finding
    end

    RS-->>AN: Cost rise not passed through to price
    AN->>RP: Findings are enough, write the report
    RP->>CTX: Read all findings
    opt Reporting needs one more figure
        RP->>DA: Current price against margin floor for brand X
        DA-->>RP: Data, source, caveats
    end
    RP->>U: Prescriptive report with link, via CX agent
```

## 3. Design points this diagram commits us to

1. **Two controlled doors.** Traffic from users and channels enters through the front gateway. Every data question goes agent, then Data agent, then the data tool gateway, then Genie. That is where limits, audit, caching and sanity checks sit.
2. **Genie stays thin.** The pricing model and optimiser run as Databricks jobs and publish tables. Genie only reads.
3. **Routing, not a script.** The CX agent hands over a confirmed request, and the post agents decide their own next steps.
4. **User identity travels with the call**, so Unity Catalog can apply the right row and column access for that person.
5. **Open design items** from the whiteboard: per-user controls and maximum query size or cost (gateway), data caching, best practice for consuming data, and the ML model. Evaluations is deferred.
