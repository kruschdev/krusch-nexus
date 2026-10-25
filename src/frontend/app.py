import streamlit as st
import requests
import os
import pandas as pd
import uuid

BACKEND_URL = os.getenv("BACKEND_URL") or os.getenv("NEXUS_API_URL") or "http://localhost:8000"
AGENT_URL = os.getenv("AGENT_URL", "http://localhost:3005")

st.set_page_config(page_title="Krusch-Nexus Institutional Knowledge", page_icon="🏦", layout="wide")

if "token" not in st.session_state:
    st.session_state.token = None
if "role" not in st.session_state:
    st.session_state.role = None

def get_headers():
    if st.session_state.token:
        return {"Authorization": f"Bearer {st.session_state.token}"}
    return {}

# Login Screen
if not st.session_state.token:
    # Allow explicit dev-mode bypass only if configured via environment
    default_dev_token = os.getenv("NEXUS_DEV_TOKEN")
    if default_dev_token:
        st.session_state.token = default_dev_token
        st.session_state.role = os.getenv("NEXUS_DEV_ROLE", "admin")
        st.rerun()
    else:
        st.title("🏦 Krusch-Nexus Institutional Knowledge")
        st.subheader("Authentication Required")
        with st.form("nexus_login_form"):
            api_token = st.text_input("API Token", type="password", placeholder="Enter your Krusch-Nexus API token...")
            role = st.selectbox("Role", ["admin", "viewer"])
            submitted = st.form_submit_button("Sign In")
            if submitted:
                if api_token and api_token.strip():
                    st.session_state.token = api_token.strip()
                    st.session_state.role = role
                    st.rerun()
                else:
                    st.error("Please provide a valid API token.")
        st.stop()

# Main Application
st.sidebar.button("Logout", on_click=lambda: st.session_state.update({"token": None, "role": None}))

st.title("🏦 Krusch-Nexus Institutional Knowledge")
st.markdown("Fully local, zero-telemetry financial RAG engine.")

# Manage Workspaces
st.sidebar.header("📁 Workspaces")

@st.cache_data(ttl=10)
def fetch_workspaces(token):
    try:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = requests.get(f"{BACKEND_URL}/api/workspaces", headers=headers, timeout=5)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 401:
            st.error("Authentication failed. Invalid API token.")
            return []
    except requests.exceptions.RequestException as e:
        st.warning(f"Unable to reach Krusch-Nexus backend: {e}")
        return []
    return []

workspaces = fetch_workspaces(st.session_state.token)

if not workspaces:
    st.sidebar.info("No workspaces available.")
else:
    workspace_names = {d["id"]: d["name"] for d in workspaces}
    selected_workspace_id = st.sidebar.selectbox("Active Workspace", options=list(workspace_names.keys()), format_func=lambda x: workspace_names[x])
    
    # Show workspace details
    selected_workspace = next((w for w in workspaces if w["id"] == selected_workspace_id), None)
    if selected_workspace and selected_workspace.get("description"):
        st.sidebar.caption(f"ℹ️ {selected_workspace['description']}")

if st.session_state.role == "admin":
    st.sidebar.divider()
    st.sidebar.subheader("Create New Workspace")
    with st.sidebar.form("new_workspace"):
        new_workspace_name = st.text_input("Workspace Name")
        new_workspace_desc = st.text_area("Description")
        if st.form_submit_button("Create Workspace"):
            try:
                res = requests.post(
                    f"{BACKEND_URL}/api/workspaces",
                    data={"name": new_workspace_name, "description": new_workspace_desc},
                    headers=get_headers(),
                    timeout=10
                )
                if res.status_code == 200:
                    st.success("Workspace created!")
                    st.rerun()
                else:
                    st.error("Failed to create workspace")
            except requests.exceptions.RequestException as e:
                st.error(f"Unable to reach Krusch-Nexus backend: {e}")

# Main Interface
if workspaces:
    if st.session_state.role == "admin":
        tabs = st.tabs(["💬 Query", "⚠️ Risk Analysis", "📜 Precedents", "📄 Upload", "🕸️ Graph Dashboard", "🌐 Global Graph Query", "🤖 Swarm", "🧑‍💼 Personal Agent"])
        tab_query, tab_risk, tab_precedent, tab_upload, tab_graph, tab_global_graph, tab_swarm, tab_agent = tabs
    else:
        tabs = st.tabs(["💬 Query", "⚠️ Risk Analysis", "📜 Precedents", "🕸️ Graph Dashboard", "🌐 Global Graph Query", "🤖 Swarm", "🧑‍💼 Personal Agent"])
        tab_query, tab_risk, tab_precedent, tab_graph, tab_global_graph, tab_swarm, tab_agent = tabs
        tab_upload = None
    
    if tab_upload is not None:
        with tab_upload:
            st.subheader("📄 Upload Documents")
            st.markdown("Supported: PDF, XLSX, CSV. Processed completely locally.")
            uploaded_file = st.file_uploader("Drop document here", type=["pdf", "xlsx", "csv", "md", "txt"])
            
            if uploaded_file is not None:
                if st.button("Ingest to Knowledge Base"):
                    with st.spinner("Parsing and embedding..."):
                        try:
                            files = {"file": (uploaded_file.name, uploaded_file, uploaded_file.type)}
                            data = {"workspace_id": selected_workspace_id}
                            res = requests.post(f"{BACKEND_URL}/api/upload", files=files, data=data, headers=get_headers(), timeout=60)
                            if res.status_code == 200:
                                st.success(f"Successfully ingested {uploaded_file.name}")
                            else:
                                st.error(f"Error: {res.text}")
                        except requests.exceptions.RequestException as e:
                            st.error(f"Upload request failed: {e}")

    with tab_query:
        st.subheader("💬 Query Institutional Knowledge")
        
        # Initialize chat history
        if "messages" not in st.session_state:
            st.session_state.messages = []

        # Display chat messages from history on app rerun
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        # Accept user input
        if prompt := st.chat_input("Ask about this workspace..."):
            # Add user message to chat history
            st.session_state.messages.append({"role": "user", "content": prompt})
            # Display user message in chat message container
            with st.chat_message("user"):
                st.markdown(prompt)

            # Display assistant response in chat message container
            with st.chat_message("assistant"):
                message_placeholder = st.empty()
                with st.spinner("Reasoning..."):
                    try:
                        payload = {"query": prompt, "workspace_id": selected_workspace_id}
                        res = requests.post(f"{BACKEND_URL}/api/query", json=payload, headers=get_headers(), timeout=30)
                        if res.status_code == 200:
                            data = res.json()
                            response_text = data.get("response", "No answer found.")
                            sources = data.get("sources", [])
                            
                            # Append sources if available
                            if sources:
                                source_list = "\n\n**Sources:**\n"
                                for src in sources:
                                    if isinstance(src, dict) and "repo_url" in src and "filepath" in src:
                                        repo_url = src["repo_url"].rstrip('/')
                                        filepath = src["filepath"]
                                        github_link = f"{repo_url}/blob/HEAD/{filepath}"
                                        source_list += f"- [{filepath}]({github_link}) (Repo: {src.get('repo', 'Unknown')})\n"
                                    elif isinstance(src, dict) and "filename" in src:
                                        page_label = src.get("page_label", src.get("page_number", ""))
                                        if page_label:
                                            source_list += f"- {src['filename']} (Page {page_label})\n"
                                        else:
                                            source_list += f"- {src['filename']}\n"
                                response_text += source_list
                                
                            # Check for compaction telemetry
                            compaction_info = None
                            for src in sources:
                                if isinstance(src, dict) and "compaction" in src:
                                    compaction_info = src["compaction"]
                                    break
                            
                            if compaction_info:
                                saved = compaction_info.get("saved_percentage", 0)
                                original = compaction_info.get("original_count", 0)
                                compacted = compaction_info.get("compacted_count", 0)
                                telemetry_md = f"""

> [!TIP]
> **⚡ Latent Briefing VRAM & Context Optimizer**
> - **Graph Context Savings Rate:** `{saved}%` token footprint reduction
> - **Relational Evidence Compaction:** Retained `{compacted}` highly relevant relationships out of `{original}` total evidence nodes.
> - **Inference Efficiency:** Optimized VRAM usage on Xeon 40T Cluster.
"""
                                response_text += telemetry_md
                                
                            message_placeholder.markdown(response_text)
                            
                            if compaction_info:
                                with st.expander("📊 View Latent Briefing Telemetry", expanded=True):
                                    col1, col2, col3 = st.columns(3)
                                    col1.metric("Context Savings Rate", f"{saved}%", "VRAM Optimized")
                                    col2.metric("Retained Relationships", f"{compacted}", f"from {original} total")
                                    col3.metric("Token Reduction Footprint", f"-{saved}%", "Aggressive")
                                    
                            st.session_state.messages.append({"role": "assistant", "content": response_text})
                        else:
                            st.error("Failed to fetch response.")
                    except Exception as e:
                        st.error(f"Error communicating with backend: {e}")

    with tab_risk:
        st.subheader("⚠️ Risk Analysis & Precedents")
        st.markdown("Use this specialized agentic workflow to analyze documents for hidden risks, obligations, and mitigation precedents.")
        
        # Initialize risk chat history
        if "risk_messages" not in st.session_state:
            st.session_state.risk_messages = []

        # Display chat messages from history on app rerun
        for message in st.session_state.risk_messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        # Accept user input
        if risk_prompt := st.chat_input("Ask about risks or precedents..."):
            st.session_state.risk_messages.append({"role": "user", "content": risk_prompt})
            with st.chat_message("user"):
                st.markdown(risk_prompt)

            with st.chat_message("assistant"):
                message_placeholder = st.empty()
                with st.spinner("Analyzing risks..."):
                    try:
                        payload = {"query": risk_prompt, "workspace_id": selected_workspace_id}
                        res = requests.post(f"{BACKEND_URL}/api/risk-analysis", json=payload, headers=get_headers())
                        if res.status_code == 200:
                            data = res.json()
                            claim = data.get("claim", "No risks found.")
                            logic = data.get("logic", "")
                            sources = data.get("sources", [])
                            
                            response_text = f"**Risk Assessment:**\n{claim}\n\n"
                            
                            if logic:
                                response_text += f"<details><summary>View Analysis Logic</summary>\n{logic}\n</details>\n\n"
                            
                            # Append sources if available
                            if sources:
                                source_list = "**Sources:**\n"
                                for src in sources:
                                    if "repo_url" in src and "filepath" in src:
                                        repo_url = src["repo_url"].rstrip('/')
                                        filepath = src["filepath"]
                                        github_link = f"{repo_url}/blob/HEAD/{filepath}"
                                        source_list += f"- [{filepath}]({github_link}) (Repo: {src.get('repo', 'Unknown')})\n"
                                    elif "filename" in src:
                                        page_label = src.get("page_label", src.get("page_number", ""))
                                        if page_label:
                                            source_list += f"- {src['filename']} (Page {page_label})\n"
                                        else:
                                            source_list += f"- {src['filename']}\n"
                                response_text += source_list
                                
                            message_placeholder.markdown(response_text, unsafe_allow_html=True)
                            st.session_state.risk_messages.append({"role": "assistant", "content": response_text})
                        else:
                            st.error(f"Failed to fetch risk analysis: {res.text}")
                    except Exception as e:
                        st.error(f"Error communicating with backend: {e}")

    with tab_precedent:
        st.subheader("📜 Precedent Analysis")
        st.markdown("Use this specialized agentic workflow to extract historical precedents and past operational decisions.")
        
        # Initialize precedent chat history
        if "precedent_messages" not in st.session_state:
            st.session_state.precedent_messages = []

        # Display chat messages from history on app rerun
        for message in st.session_state.precedent_messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        # Accept user input
        if precedent_prompt := st.chat_input("Ask about historical precedents..."):
            st.session_state.precedent_messages.append({"role": "user", "content": precedent_prompt})
            with st.chat_message("user"):
                st.markdown(precedent_prompt)

            with st.chat_message("assistant"):
                message_placeholder = st.empty()
                with st.spinner("Analyzing precedents..."):
                    try:
                        payload = {"query": precedent_prompt, "workspace_id": selected_workspace_id}
                        res = requests.post(f"{BACKEND_URL}/api/precedent-analysis", json=payload, headers=get_headers())
                        if res.status_code == 200:
                            data = res.json()
                            claim = data.get("claim", "No precedents found.")
                            logic = data.get("logic", "")
                            sources = data.get("sources", [])
                            
                            response_text = f"**Precedent Report:**\n{claim}\n\n"
                            
                            if logic:
                                response_text += f"<details><summary>View Historical Context</summary>\n{logic}\n</details>\n\n"
                            
                            # Append sources if available
                            if sources:
                                source_list = "**Sources:**\n"
                                for src in sources:
                                    if "repo_url" in src and "filepath" in src:
                                        repo_url = src["repo_url"].rstrip('/')
                                        filepath = src["filepath"]
                                        github_link = f"{repo_url}/blob/HEAD/{filepath}"
                                        source_list += f"- [{filepath}]({github_link}) (Repo: {src.get('repo', 'Unknown')})\n"
                                    elif "filename" in src:
                                        page_label = src.get("page_label", src.get("page_number", ""))
                                        if page_label:
                                            source_list += f"- {src['filename']} (Page {page_label})\n"
                                        else:
                                            source_list += f"- {src['filename']}\n"
                                response_text += source_list
                                
                            message_placeholder.markdown(response_text, unsafe_allow_html=True)
                            st.session_state.precedent_messages.append({"role": "assistant", "content": response_text})
                        else:
                            st.error(f"Failed to fetch precedent analysis: {res.text}")
                    except Exception as e:
                        st.error(f"Error communicating with backend: {e}")

    with tab_graph:
        st.subheader("🕸️ Graph Dashboard")
        st.markdown("Visualizing entities and relationships for this workspace.")
        
        try:
            graph_res = requests.get(f"{BACKEND_URL}/api/graph/{selected_workspace_id}", headers=get_headers())
            if graph_res.status_code == 200:
                graph_data = graph_res.json()
                nodes = graph_data.get("nodes", [])
                edges = graph_data.get("edges", [])
                
                if not nodes and not edges:
                    st.info("No graph data extracted yet for this workspace.")
                else:
                    st.write(f"**Entities ({len(nodes)})**")
                    df_nodes = pd.DataFrame(nodes)
                    st.dataframe(df_nodes, use_container_width=True)
                    st.download_button(
                        label="📥 Export Entities to CSV",
                        data=df_nodes.to_csv(index=False).encode("utf-8"),
                        file_name=f"workspace_{selected_workspace_id}_entities.csv",
                        mime="text/csv",
                        key="export_entities"
                    )
                    
                    st.write(f"**Relationships ({len(edges)})**")
                    df_edges = pd.DataFrame(edges)
                    st.dataframe(df_edges, use_container_width=True)
                    st.download_button(
                        label="📥 Export Relationships to CSV",
                        data=df_edges.to_csv(index=False).encode("utf-8"),
                        file_name=f"workspace_{selected_workspace_id}_relationships.csv",
                        mime="text/csv",
                        key="export_edges"
                    )
            else:
                st.error("Failed to fetch graph data.")
        except Exception as e:
            st.error(f"Error connecting to backend: {e}")
            
    with tab_global_graph:
        st.subheader("🌐 Global Graph Query")
        st.markdown("Query the knowledge graph across multiple workspaces.")
        st.info("Leave the selection empty to query relationships across ALL workspaces.", icon="ℹ️")
        
        all_workspace_ids = list(workspace_names.keys())
        selected_multi_workspaces = st.multiselect(
            "Select Workspaces to Compare",
            options=all_workspace_ids,
            default=all_workspace_ids,
            format_func=lambda x: workspace_names.get(x, str(x))
        )
        
        if "global_messages" not in st.session_state:
            st.session_state.global_messages = []
            
        for message in st.session_state.global_messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
                
        if global_prompt := st.chat_input("Compare entities across selected workspaces..."):
            st.session_state.global_messages.append({"role": "user", "content": global_prompt})
            with st.chat_message("user"):
                st.markdown(global_prompt)
                
            with st.chat_message("assistant"):
                message_placeholder = st.empty()
                with st.spinner("Reasoning across workspaces..."):
                    try:
                        payload = {"query": global_prompt, "workspace_ids": selected_multi_workspaces}
                        res = requests.post(f"{BACKEND_URL}/api/query-cross-workspace", json=payload, headers=get_headers())
                        if res.status_code == 200:
                            data = res.json()
                            response_text = data.get("response", "No answer found.")
                            sources = data.get("sources", [])
                            
                            # Check for compaction telemetry
                            compaction_info = None
                            for src in sources:
                                if isinstance(src, dict) and "compaction" in src:
                                    compaction_info = src["compaction"]
                                    break
                                    
                            if compaction_info:
                                saved = compaction_info.get("saved_percentage", 0)
                                original = compaction_info.get("original_count", 0)
                                compacted = compaction_info.get("compacted_count", 0)
                                telemetry_md = f"""

> [!TIP]
> **⚡ Latent Briefing VRAM & Context Optimizer**
> - **Graph Context Savings Rate:** `{saved}%` token footprint reduction
> - **Relational Evidence Compaction:** Retained `{compacted}` highly relevant relationships out of `{original}` total evidence nodes.
> - **Inference Efficiency:** Optimized VRAM usage on Xeon 40T Cluster.
"""
                                response_text += telemetry_md
                                
                            message_placeholder.markdown(response_text)
                            
                            if compaction_info:
                                with st.expander("📊 View Latent Briefing Telemetry", expanded=True):
                                    col1, col2, col3 = st.columns(3)
                                    col1.metric("Context Savings Rate", f"{saved}%", "VRAM Optimized")
                                    col2.metric("Retained Relationships", f"{compacted}", f"from {original} total")
                                    col3.metric("Token Reduction Footprint", f"-{saved}%", "Aggressive")
                                    
                            st.session_state.global_messages.append({"role": "assistant", "content": response_text})
                        else:
                            st.error(f"Failed to fetch response: {res.text}")
                    except Exception as e:
                        st.error(f"Error communicating with backend: {e}")
    with tab_swarm:
        st.subheader("🤖 DBOS Swarm Queue")
        st.markdown("Live view of the autonomous debate swarm. Workers generate ideas, critique them, synthesize hardened prototypes, and surface them here for human review.")
        
        # --- Stats Row ---
        try:
            stats_res = requests.get(f"{BACKEND_URL}/api/swarm/stats", headers=get_headers())
            if stats_res.status_code == 200:
                stats = stats_res.json()
                if "error" not in stats:
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("📊 Total Jobs", stats.get("total_jobs", 0))
                    col2.metric("⏳ Pending", stats.get("pending", 0))
                    col3.metric("✅ Completed", stats.get("completed", 0))
                    col4.metric("👁️ Pending Reviews", stats.get("pending_reviews", 0))
                    
                    col5, col6, col7 = st.columns(3)
                    col5.metric("💡 Proposals", stats.get("proposals", 0))
                    col6.metric("🔍 Rebuttals", stats.get("rebuttals", 0))
                    col7.metric("📝 Reviews", stats.get("human_reviews", 0))
                else:
                    st.warning("Could not connect to DBOS queue database.")
            else:
                st.error("Failed to fetch swarm stats.")
        except Exception as e:
            st.error(f"Error connecting to backend: {e}")
        
        st.divider()
        
        # --- Human Reviews Section ---
        st.subheader("👁️ Pending Human Reviews")
        st.markdown("These synthesized prototype plans have been debated and refined by the swarm. Review and take action.")
        
        try:
            reviews_res = requests.get(f"{BACKEND_URL}/api/swarm/reviews", headers=get_headers())
            if reviews_res.status_code == 200:
                reviews = reviews_res.json()
                if not reviews:
                    st.info("No pending reviews. The swarm is still debating...")
                else:
                    for review in reviews:
                        job_id = review["job_id"]
                        payload = review.get("payload", {})
                        instructions = payload.get("instructions", "No instructions provided.")
                        context = payload.get("context", {})
                        dispatched_by = context.get("dispatched_by", "unknown")
                        target_node = context.get("target_node", "any")
                        started_at = review.get("started_at", "N/A")
                        
                        with st.expander(f"📋 Review {job_id[:8]}... — from {dispatched_by}", expanded=True):
                            st.caption(f"🕐 Submitted: {started_at} | 🎯 Target: {target_node}")
                            st.markdown(instructions)
                            
                            # Thread drill-down (toggle instead of nested expander)
                            thread_id = review.get("thread_id")
                            if thread_id:
                                show_thread = st.toggle("🔗 View Full Debate Thread", key=f"thread_{job_id}")
                                if show_thread:
                                    thread_res = requests.get(f"{BACKEND_URL}/api/swarm/thread/{thread_id}", headers=get_headers())
                                    if thread_res.status_code == 200:
                                        thread_jobs = thread_res.json()
                                        for tj in thread_jobs:
                                            tj_type = tj.get("job_type", "unknown")
                                            tj_status = tj.get("status", "unknown")
                                            tj_payload = tj.get("payload", {})
                                            tj_result = tj.get("result", {})
                                            
                                            type_icons = {
                                                "debate_proposal": "💡",
                                                "debate_rebuttal": "🔍",
                                                "human_review": "👁️"
                                            }
                                            icon = type_icons.get(tj_type, "📦")
                                            
                                            st.markdown(f"**{icon} {tj_type}** ({tj_status})")
                                            
                                            if tj_type == "debate_proposal":
                                                idea = tj_payload.get("idea", "")
                                                node = tj_payload.get("node", "unknown")
                                                st.markdown(f"*From node: {node}*")
                                                st.text_area("Original Idea", idea, height=100, key=f"idea_{tj['job_id']}", disabled=True)
                                                if tj_result and tj_result.get("critique"):
                                                    st.text_area("Critique", tj_result["critique"], height=100, key=f"critique_{tj['job_id']}", disabled=True)
                                            elif tj_type == "debate_rebuttal":
                                                if tj_result and tj_result.get("finalPlan"):
                                                    st.text_area("Synthesized Plan", tj_result["finalPlan"], height=150, key=f"plan_{tj['job_id']}", disabled=True)
                                            
                                            st.markdown("---")
                            
                            # Action buttons (admin only)
                            if st.session_state.role == "admin":
                                action_cols = st.columns(3)
                                with action_cols[0]:
                                    if st.button("✅ Approve", key=f"approve_{job_id}", type="primary"):
                                        res = requests.post(f"{BACKEND_URL}/api/swarm/review/{job_id}", json={"status": "approved"}, headers=get_headers())
                                        if res.status_code == 200:
                                            st.success("Approved!")
                                            st.rerun()
                                        else:
                                            st.error("Failed to approve.")
                                with action_cols[1]:
                                    if st.button("❌ Reject", key=f"reject_{job_id}"):
                                        res = requests.post(f"{BACKEND_URL}/api/swarm/review/{job_id}", json={"status": "rejected"}, headers=get_headers())
                                        if res.status_code == 200:
                                            st.warning("Rejected.")
                                            st.rerun()
                                        else:
                                            st.error("Failed to reject.")
                                with action_cols[2]:
                                    if st.button("🗑️ Dismiss", key=f"dismiss_{job_id}"):
                                        res = requests.post(f"{BACKEND_URL}/api/swarm/review/{job_id}", json={"status": "dismissed"}, headers=get_headers())
                                        if res.status_code == 200:
                                            st.info("Dismissed.")
                                            st.rerun()
                                        else:
                                            st.error("Failed to dismiss.")
        except Exception as e:
            st.error(f"Error fetching reviews: {e}")
        
        st.divider()
        
        # --- Full Job Queue ---
        st.subheader("📊 All Swarm Jobs")
        
        filter_cols = st.columns(3)
        with filter_cols[0]:
            filter_status = st.selectbox("Filter by Status", ["all", "pending", "running", "completed", "approved", "rejected", "dismissed"], key="swarm_filter_status")
        with filter_cols[1]:
            filter_type = st.selectbox("Filter by Type", ["all", "debate_proposal", "debate_rebuttal", "human_review"], key="swarm_filter_type")
        with filter_cols[2]:
            filter_limit = st.number_input("Max Results", min_value=5, max_value=200, value=50, key="swarm_filter_limit")
        
        try:
            params = {"limit": filter_limit}
            if filter_status != "all":
                params["status"] = filter_status
            if filter_type != "all":
                params["job_type"] = filter_type
            
            jobs_res = requests.get(f"{BACKEND_URL}/api/swarm/jobs", params=params, headers=get_headers())
            if jobs_res.status_code == 200:
                jobs = jobs_res.json()
                if jobs:
                    # Build display table
                    table_data = []
                    for j in jobs:
                        status_icons = {"pending": "⏳", "running": "🔄", "completed": "✅", "approved": "✅", "rejected": "❌", "dismissed": "🗑️"}
                        type_icons = {"debate_proposal": "💡", "debate_rebuttal": "🔍", "human_review": "👁️"}
                        
                        table_data.append({
                            "Status": f"{status_icons.get(j['status'], '❓')} {j['status']}",
                            "Type": f"{type_icons.get(j['job_type'], '📦')} {j['job_type']}",
                            "Job ID": j["job_id"][:12] + "...",
                            "Thread": j["thread_id"][:8] + "...",
                            "Started": j.get("started_at", "N/A")[:19],
                        })
                    
                    df = pd.DataFrame(table_data)
                    st.dataframe(df, use_container_width=True, hide_index=True)
                else:
                    st.info("No jobs found matching the filters.")
            else:
                st.error("Failed to fetch swarm jobs.")
        except Exception as e:
            st.error(f"Error fetching jobs: {e}")

    with tab_agent:
        st.subheader("🧑‍💼 Personal Agent")
        st.markdown("Interact directly with the Hermes 3 / Gemma autonomous homelab agent.")
        
        # Initialize agent chat history
        if "agent_messages" not in st.session_state:
            st.session_state.agent_messages = []
            
        # Initialize persistent agent conversation ID
        if "agent_conversation_id" not in st.session_state:
            st.session_state.agent_conversation_id = str(uuid.uuid4())

        # Display chat messages from history
        for message in st.session_state.agent_messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        # Accept user input
        if agent_prompt := st.chat_input("Command the homelab..."):
            st.session_state.agent_messages.append({"role": "user", "content": agent_prompt})
            with st.chat_message("user"):
                st.markdown(agent_prompt)

            with st.chat_message("assistant"):
                message_placeholder = st.empty()
                with st.spinner("Agent is reasoning and using tools..."):
                    try:
                        payload = {
                            "conversationId": st.session_state.agent_conversation_id,
                            "message": agent_prompt
                        }
                        res = requests.post(f"{AGENT_URL}/chat", json=payload, headers=get_headers())
                        if res.status_code == 200:
                            data = res.json()
                            response_text = data.get("reply", "No response from agent.")
                            message_placeholder.markdown(response_text)
                            st.session_state.agent_messages.append({"role": "assistant", "content": response_text})
                        else:
                            st.error(f"Agent error: {res.text}")
                    except Exception as e:
                        st.error(f"Error communicating with agent: {e}")

else:
    if st.session_state.role == "admin":
        st.info("👈 Create a workspace in the sidebar to get started.")
    else:
        st.info("No workspaces available. Please contact an admin.")
