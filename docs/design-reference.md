The Dev-Grade Architecture (Using ADK)
text
flowchart TD
    U["End User (App / API)"] -->|"HTTPS Request"| AE["Vertex AI Agent Engine\n(Managed Runtime)"]

    subgraph ADK ["ADK Multi-Agent System"]
        RT["Router Agent\n(ADK Supervisor)"]
        
        RT -->|"Structured Query"| DBA["Database Agent\n(ADK + Gemini)"]
        RT -->|"Unstructured Query"| RAGA["RAG Agent\n(ADK + Gemini)"]
    end

    AE --> RT

    %% Database Agent Flow using MCP
    subgraph Tooling ["MCP Toolbox (Cloud Run)"]
        MCP["MCP Server for Databases\n(tools.yaml)"]
    end

    DBA -->|"Calls tool via MCP Protocol"| MCP
    MCP -->|"Secure Network (VPN)"| DB[("Client SQL Server\nRead-only")]
    DB -->|"Result Set"| MCP
    MCP --> MASK["Presidio PII Masking\n(Middleware)"]
    MASK -->|"Masked JSON"| DBA

    %% RAG Agent Flow using Vertex AI RAG Engine
    subgraph RAG_Engine ["Vertex AI RAG Engine"]
        CORPUS["RAG Corpus\n(Managed Index)"]
    end

    RAGA -->|"Semantic Search Tool"| CORPUS
    
    %% Ingestion Pipeline
    GCS["Cloud Storage\nRaw Docs"] -->|"Chunk & Embed"| CORPUS

    %% Config
    DBA -.-> SM["Secret Manager\n(DB Credentials)"]
    RT -.-> FS["Firestore\n(Tenant Config)"]
How to build this step-by-step in Dev:
Install ADK:
Install the official Google ADK package in your Python environment:

bash
pip install google-adk mcp
Set up the MCP Toolbox (for Text-to-SQL):

Deploy the open-source MCP Toolbox for Databases to Cloud Run.

Define a tools.yaml file mapping out the safe, read-only SQL Server queries you allow the LLM to run.

Point the MCP Toolbox at your SQL Server instance via a VPC connector.

Build the ADK Agents:

DB Agent: Give it the ToolboxToolset connected to your MCP server. It will automatically understand the SQL Server schema tools you defined in YAML.

RAG Agent: Give it a RagCorpusTool that connects to Vertex AI RAG Engine (which handles the chunking, embedding, and vector storage for you, completely replacing the need to manage a raw pgvector database yourself).

Router Agent: Use ADK's orchestration to make this the entry point. It evaluates the user's prompt and hands off the session to the DB Agent or the RAG Agent.

Deploy:

Deploy your ADK python code directly to Vertex AI Agent Engine. It will wrap your agents in a scalable API, provide out-of-the-box evaluation metrics, and handle the multi-tenant session state.

Using ADK + MCP Toolbox is the most modern, Google-recommended way to build this exact "Rack system" today. It separates the AI logic (ADK) from the database infrastructure logic (MCP Toolbox), making it incredibly easy to plug in new clients later.








GitHub Repos to Reference
Here are the exact repos — ordered by how directly they match your use case:

1. Official MCP Toolbox for Databases
🔗 
https://github.com/googleapis/genai-toolbox

The core open-source MCP server you will deploy to Cloud Run. Has SQL Server support, tools.yaml examples, connection pooling, and auth.

2. Official Codelab Repo (ADK + MCP Toolbox + Cloud SQL + pgvector)
🔗 
https://github.com/paulramsey/adk-toolbox-agent

This is the hands-on reference for exactly what you are building. It shows how to write tools.yaml, load toolsets in ADK, deploy to Cloud Run, and run vector search via pgvector.

3. ADK + Vertex AI RAG Engine
🔗 
https://github.com/arjunprabhulal/adk-vertex-ai-rag-engine

Shows how to hook up the document RAG path using RagCorpusTool inside an ADK agent connected to Vertex AI RAG Engine — covers the unstructured docs side of your design.
​

4. ADK RAG Agent (Vertex AI)
🔗 
https://github.com/bhancockio/adk-rag-agent

A clean, minimal ADK RAG agent using Vertex AI — good starting point for understanding the agent code structure before adding multi-tenancy.
​

5. ADK + MCP + Qdrant RAG
🔗 
https://github.com/khoi03/adk-mcp-rag

Shows how to combine ADK + MCP + a vector database for RAG. While it uses Qdrant instead of Cloud SQL, the agent code pattern is identical — swap the vector store for yours.
​

6. Official ADK Sample Agents
🔗 
https://github.com/google/adk-samples

Google's official collection of production-ready ADK agent samples across many use cases — good for understanding multi-agent orchestration patterns.
​

Recommended Build Order for Your Dev Environment
Start with Repo #2 (adk-toolbox-agent) — get the MCP Toolbox running against Cloud SQL PostgreSQL locally first, even before touching SQL Server. This proves the end-to-end Text-to-SQL flow works in your GCP project.

Swap the source in tools.yaml from cloud-sql-postgres to mssql (SQL Server) once the Postgres version is confirmed working.

Add the RAG Agent from Repo #3 as a second agent under your Router Agent.

Add the Firestore tenant config and Secret Manager lookups last — this upgrades it from single-client dev to a proper multi-tenant rack.