import streamlit as st
import requests
import os
import pandas as pd
import uuid
import datetime

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
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
    # Bypass auth for homelab testing
    st.session_state.token = "homelab_bypass"
    st.session_state.role = "admin"
    st.rerun()

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
        response = requests.get(f"{BACKEND_URL}/api/workspaces", headers=headers)
        if response.status_code == 200:
            return response.json()
    except Exception:
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
            res = requests.post(f"{BACKEND_URL}/api/workspaces", data={"name": new_workspace_name, "description": new_workspace_desc}, headers=get_headers())
            if res.status_code == 200:
                st.success("Workspace created!")
                st.rerun()
            else:
                st.error("Failed to create workspace")

# Main Interface
if workspaces:
    ui_mode = st.sidebar.radio("Navigation View", ["👤 User Portal (Beta)", "🛠️ Admin & Engineering Hub"], index=0)
    st.sidebar.divider()
    
    if ui_mode == "👤 User Portal (Beta)":
        tabs = st.tabs(["💬 Query Knowledge", "📄 Upload & Documents", "💼 Business Suite"])
        tab_query, tab_upload, tab_business = tabs
        tab_risk = tab_precedent = tab_graph = tab_global_graph = tab_swarm = tab_agent = tab_sync = tab_alignment = None
    else:
        tabs = st.tabs(["💬 Query", "⚠️ Risk Analysis", "📜 Precedents", "📄 Upload", "💼 Business Suite", "🕸️ Graph Dashboard", "🌐 Global Graph Query", "🤖 Swarm", "🧑‍💼 Personal Agent", "🔄 Sync Center", "🎯 Alignment Hub"])
        tab_query, tab_risk, tab_precedent, tab_upload, tab_business, tab_graph, tab_global_graph, tab_swarm, tab_agent, tab_sync, tab_alignment = tabs

    
    if tab_upload is not None:
        with tab_upload:
            st.subheader("📄 Upload Documents")
            st.markdown("Supported: PDF, XLSX, CSV. Processed completely locally.")
            uploaded_file = st.file_uploader("Drop document here", type=["pdf", "xlsx", "csv", "md", "txt"])
            
            if uploaded_file is not None:
                if st.button("Ingest to Knowledge Base"):
                    with st.spinner("Parsing and embedding..."):
                        files = {"file": (uploaded_file.name, uploaded_file, uploaded_file.type)}
                        data = {"workspace_id": selected_workspace_id}
                        res = requests.post(f"{BACKEND_URL}/api/upload", files=files, data=data, headers=get_headers())
                        if res.status_code == 200:
                            st.success(f"Successfully ingested {uploaded_file.name}")
                        else:
                            st.error(f"Error: {res.text}")

    with tab_query:
        st.subheader("💬 Query Institutional Knowledge")
        
        # Initialize chat history
        if "messages" not in st.session_state:
            st.session_state.messages = []
            
        if "pending_action" not in st.session_state:
            st.session_state.pending_action = None

        # Display chat messages from history on app rerun
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
                g_info = message.get("guardrails", {})
                if g_info and g_info.get("triggered"):
                    risk = g_info.get("risk_level", "HIGH")
                    matches = g_info.get("matches", [])
                    if risk == "CRITICAL":
                        st.error(f"🚨 **Critical Legal Risk Detected (UPL Shield)**: Matches: {matches}")
                    else:
                        st.warning(f"⚠️ **Elevated Legal Topic Match (UPL Shield)**: Matches: {matches}")
                
                p_nudge = message.get("proactive_nudge")
                if p_nudge:
                    st.info(p_nudge)

        # Render Pending Proposed Action Card (if any) at the bottom
        if st.session_state.pending_action:
            action = st.session_state.pending_action
            st.markdown(f"### 🤖 Proposed Business Tool Execution: **{action['tool'].replace('_', ' ').title()}**")
            st.markdown("Please approve or deny the parameter details below before execution:")
            st.json(action["params"])
            
            col_app, col_den = st.columns(2)
            with col_app:
                if st.button("✅ Approve & Run Action", key="btn_approve_action", type="primary"):
                    with st.spinner("Executing approved tool..."):
                        try:
                            payload = {
                                "query": action["query"],
                                "workspace_id": selected_workspace_id,
                                "approved_tool": action["tool"],
                                "approved_params": action["params"]
                            }
                            res = requests.post(f"{BACKEND_URL}/api/query", json=payload, headers=get_headers())
                            if res.status_code == 200:
                                data = res.json()
                                response_text = data.get("response", "No answer found.")
                                guardrails_info = data.get("guardrails", {})
                                
                                st.session_state.messages.append({
                                    "role": "assistant",
                                    "content": response_text,
                                    "guardrails": guardrails_info
                                })
                                st.session_state.pending_action = None
                                st.rerun()
                            else:
                                st.error(f"Execution failed: {res.text}")
                        except Exception as e:
                            st.error(f"Error communicating with backend: {e}")
            with col_den:
                if st.button("❌ Deny Action", key="btn_deny_action"):
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": f"❌ Action denied by user: {action['tool']}."
                    })
                    st.session_state.pending_action = None
                    st.rerun()
                    
            st.divider()

        # Accept user input (only if no action is pending to enforce gating)
        if st.session_state.pending_action:
            st.info("Please resolve the pending action proposal above before entering a new query.")
        else:
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
                            res = requests.post(f"{BACKEND_URL}/api/query", json=payload, headers=get_headers())
                            
                            nudge_text = None
                            try:
                                nudge_res = requests.post(f"{BACKEND_URL}/api/proactive-nudge", json=payload, headers=get_headers())
                                if nudge_res.status_code == 200:
                                    nudge_val = nudge_res.json().get("nudge", "")
                                    if nudge_val and "NO_NUDGES_REQUIRED" not in nudge_val:
                                        nudge_text = nudge_val
                            except Exception as e:
                                print(f"Proactive audit query failed: {e}")

                            if res.status_code == 200:
                                data = res.json()
                                response_text = data.get("response", "No answer found.")
                                sources = data.get("sources", [])
                                guardrails_info = data.get("guardrails", {})
                                proposed = data.get("proposed_action")
                                
                                # If backend proposed a tool, set state and note it
                                if proposed:
                                    st.session_state.pending_action = {
                                        "tool": proposed["tool"],
                                        "params": proposed["params"],
                                        "query": prompt
                                    }
                                    response_text = f"🤖 **Action Proposed**: I need your authorization to run the business tool **{proposed['tool'].replace('_', ' ').title()}**."
                                
                                # Append sources if available (only for non-proposed normal replies)
                                if not proposed and sources:
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
                                        
                                # Render guardrail warning box immediately if triggered
                                if guardrails_info and guardrails_info.get("triggered"):
                                    risk = guardrails_info.get("risk_level", "HIGH")
                                    matches = guardrails_info.get("matches", [])
                                    if risk == "CRITICAL":
                                        st.error(f"🚨 **Critical Legal Risk Detected (UPL Shield)**: Matches: {matches}")
                                    else:
                                        st.warning(f"⚠️ **Elevated Legal Topic Match (UPL Shield)**: Matches: {matches}")
                                        
                                if nudge_text:
                                    st.info(nudge_text)
                                        
                                st.session_state.messages.append({
                                    "role": "assistant",
                                    "content": response_text,
                                    "guardrails": guardrails_info,
                                    "proactive_nudge": nudge_text
                                })
                                
                                # Rerun to show proposed action form immediately
                                if proposed:
                                    st.rerun()
                            else:
                                st.error("Failed to fetch response.")
                        except Exception as e:
                            st.error(f"Error communicating with backend: {e}")


    if tab_risk is not None:
        with tab_risk:
            st.subheader("⚠️ Risk Analysis & Coordination Feed")
        sub_tab_chat, sub_tab_feed = st.tabs(["💬 Risk Chat", "🧠 Internal Coordination Feed"])
        
        with sub_tab_chat:
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

        with sub_tab_feed:
            st.markdown("### 🧠 Company Brain: Coordination Feed")
            st.markdown("This feed displays proactive conflicts, stale departmental assumptions, or coordination gaps automatically detected by the background Coordination Auditor.")
            
            try:
                feed_res = requests.get(f"{BACKEND_URL}/api/coordination/conflicts/{selected_workspace_id}", headers=get_headers())
                if feed_res.status_code == 200:
                    conflicts_list = feed_res.json()
                    if not conflicts_list:
                        st.success("✓ Shared State is fully aligned. No coordination conflicts detected.")
                    else:
                        for conflict in conflicts_list:
                            with st.container(border=True):
                                col_title, col_btn = st.columns([4, 1])
                                with col_title:
                                    st.markdown(f"#### ⚠️ {conflict['title']}")
                                with col_btn:
                                    if st.button("Mark Resolved", key=f"resolve_{conflict['id']}"):
                                        resolve_res = requests.post(f"{BACKEND_URL}/api/coordination/conflicts/resolve/{conflict['id']}", headers=get_headers())
                                        if resolve_res.status_code == 200:
                                            st.success("Resolved!")
                                            st.rerun()
                                        else:
                                            st.error("Failed to resolve.")
                                
                                st.markdown(f"**Discovered Conflict:** {conflict['claim']}")
                                st.markdown(f"**Audit Logic:**\n{conflict['logic']}")
                                
                                if conflict.get("sources"):
                                    st.markdown("**Conflicting Artifacts:**")
                                    for src in conflict["sources"]:
                                        st.markdown(f"- 📄 `{src['filename']}`")
                                        
                                st.caption(f"Detected at: {conflict['created_at']}")
                else:
                    st.error(f"Failed to fetch coordination feed: {feed_res.text}")
            except Exception as e:
                st.error(f"Error fetching coordination feed: {e}")

    if tab_precedent is not None:
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

    if tab_graph is not None:
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
            
    if tab_global_graph is not None:
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
    if tab_swarm is not None:
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

    if tab_agent is not None:
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

    if tab_sync is not None:
        with tab_sync:
            st.subheader("🔄 Workspace & Directory Synchronization")
        st.markdown(
            "Normalize, import, and search corporate directories, emails, and shared folders locally."
        )

        col1, col2 = st.columns([1, 2])

        with col1:
            st.info("⚙️ **Sync Settings**")
            provider = st.selectbox("Sync Provider", ["Local JSON", "Google Workspace"])
            
            st.write("---")
            st.markdown("### 🛠️ Actions")
            
            # Sync directory button
            if st.button("Sync Directory Users", key="btn_sync_dir"):
                with st.spinner("Synchronizing directory..."):
                    try:
                        payload = {"provider": "google" if provider == "Google Workspace" else "local"}
                        res = requests.post(f"{BACKEND_URL}/api/sync/directory", json=payload, headers=get_headers())
                        if res.status_code == 200:
                            data = res.json()
                            st.success(f"Synced successfully! Created: {data.get('created', 0)}, Updated: {data.get('updated', 0)}")
                            st.rerun()
                        else:
                            st.error(f"Sync failed: {res.text}")
                    except Exception as ex:
                        st.error(f"Error triggering sync: {ex}")

            # Sync documents button
            if workspaces:
                if st.button("Sync Emails & Files", key="btn_sync_docs"):
                    with st.spinner("Downloading and indexing documents locally..."):
                        try:
                            payload = {
                                "provider": "google" if provider == "Google Workspace" else "local",
                                "workspace_id": selected_workspace_id
                            }
                            res = requests.post(f"{BACKEND_URL}/api/sync/documents", json=payload, headers=get_headers())
                            if res.status_code == 200:
                                data = res.json()
                                st.success(
                                    f"Synced docs successfully! Emails synced: {data.get('emails_synced', 0)}, Files synced: {data.get('files_synced', 0)}"
                                )
                                st.rerun()
                            else:
                                st.error(f"Doc sync failed: {res.text}")
                        except Exception as ex:
                            st.error(f"Error triggering document sync: {ex}")
            else:
                st.warning("Create/select a workspace in the sidebar to sync documents.")

        with col2:
            st.markdown("### 🧑‍💼 Synchronized Employee Directory")
            try:
                res = requests.get(f"{BACKEND_URL}/api/employees", headers=get_headers())
                if res.status_code == 200:
                    employees = res.json()
                    if employees:
                        # Convert to pandas DataFrame
                        df = pd.DataFrame(employees)
                        df.rename(columns={
                            "provider": "Provider",
                            "display_name": "Full Name",
                            "email": "Email Address",
                            "job_title": "Role/Title",
                            "department": "Department",
                            "status": "Status"
                        }, inplace=True)
                        
                        # Display clean metric card
                        st.metric("Total Employees Synchronized", len(employees))
                        
                        # Render DataFrame with nice formatting
                        st.dataframe(
                            df[["Full Name", "Role/Title", "Department", "Email Address", "Provider", "Status"]],
                            use_container_width=True,
                            hide_index=True
                        )
                    else:
                        st.info("No employees synchronized yet. Select a provider and click 'Sync Directory Users'.")
                else:
                    st.error("Failed to load employee list from backend.")
            except Exception as ex:
                st.error(f"Error fetching employees: {ex}")

    if tab_business is not None:
        with tab_business:
            st.subheader("💼 Pocket Lawyer Small Business Suite")
            st.markdown("Fully local, air-gapped legal and compliance assistant tools for Small Business Pro.")

            # Sub-tabs within the Business Suite tab
            sub_tabs = st.tabs([
                "🏢 Company Profile", 
                "📝 Contract Reviewer", 
                "📅 Compliance Calendar", 
                "👥 HR & Onboarding", 
                "⚖️ Dispute & Collections",
                "🧑‍💻 Employee Suite"
            ])
            sub_profile, sub_contract, sub_calendar, sub_hr, sub_dispute, sub_employee = sub_tabs

            # Get existing profile
            profile = {}
            try:
                p_res = requests.get(f"{BACKEND_URL}/api/business-pro/profile", headers=get_headers())
                if p_res.status_code == 200 and p_res.json().get("status") == "success":
                    profile = p_res.json().get("profile", {})
            except Exception:
                pass

            with sub_profile:
                st.markdown("### 🏢 Business Profile System")
                st.markdown("Configure your profile to personalize AI tool results and document generation.")
                
                with st.form("business_profile_form"):
                    company_name = st.text_input("Company Name", value=profile.get("company_name", ""))
                    col1, col2 = st.columns(2)
                    with col1:
                        entity_type = st.selectbox("Entity Type", ["LLC", "C-Corp", "S-Corp", "Partnership", "Sole Proprietorship"], index=["LLC", "C-Corp", "S-Corp", "Partnership", "Sole Proprietorship"].index(profile.get("entity_type", "LLC")) if profile.get("entity_type") in ["LLC", "C-Corp", "S-Corp", "Partnership", "Sole Proprietorship"] else 0)
                        ein = st.text_input("Employer Identification Number (EIN)", value=profile.get("ein", ""))
                    with col2:
                        state = st.selectbox("State of Incorporation", ["California", "Delaware", "New York", "Texas", "Florida"], index=["California", "Delaware", "New York", "Texas", "Florida"].index(profile.get("state", "California")) if profile.get("state") in ["California", "Delaware", "New York", "Texas", "Florida"] else 0)
                        employee_count = st.number_input("Employee Count", min_value=0, value=int(profile.get("employee_count", 0)))
                    
                    industry = st.text_input("Industry / Sector", value=profile.get("industry", ""))
                    services = st.text_area("Services or Products Offered", value=profile.get("services", ""))
                    compliance_licenses = st.text_area("Current Licenses & Permits", value=profile.get("compliance_licenses", ""))
                    goals = st.text_area("Legal & Compliance Goals", value=profile.get("goals", ""))
                    notes = st.text_area("Additional Context", value=profile.get("notes", ""))
                    
                    submitted = st.form_submit_button("Save Profile")
                    if submitted:
                        payload = {
                            "company_name": company_name,
                            "entity_type": entity_type,
                            "ein": ein,
                            "state": state,
                            "employee_count": employee_count,
                            "industry": industry,
                            "services": services,
                            "compliance_licenses": compliance_licenses,
                            "goals": goals,
                            "notes": notes,
                            "user_id": 1
                        }
                        try:
                            res = requests.post(f"{BACKEND_URL}/api/business-pro/profile", json=payload, headers=get_headers())
                            if res.status_code == 200:
                                st.success("Profile saved successfully!")
                                st.rerun()
                            else:
                                st.error(f"Error: {res.text}")
                        except Exception as e:
                            st.error(f"Failed to connect to backend: {e}")

                # Completeness check
                fields = [company_name, ein, industry, services]
                filled = sum(1 for f in fields if f)
                completeness = int((filled / len(fields)) * 100)
                st.progress(completeness / 100)
                st.caption(f"Profile Completeness: {completeness}%")

            with sub_contract:
                st.markdown("### 📝 Smart Contract Reviewer")
                st.markdown("Analyze agreements for liability exposure, indemnity issues, and automatic renewals.")
                
                contract_type = st.selectbox(
                    "Contract Type", 
                    ["service_agreement", "nda", "employment_contract", "lease", "vendor_agreement", "general"],
                    key="business_contract_type_select"
                )
                contract_text = st.text_area("Paste Contract Text here (Markdown or Plain Text)", height=300, key="business_contract_text_area")
                
                if st.button("Analyze Contract"):
                    if not contract_text:
                        st.warning("Please paste contract text first.")
                    else:
                        with st.spinner("Analyzing clauses..."):
                            payload = {"contract_text": contract_text, "contract_type": contract_type}
                            try:
                                res = requests.post(f"{BACKEND_URL}/api/business-pro/tools/contract-review", json=payload, headers=get_headers())
                                if res.status_code == 200:
                                    analysis = res.json().get("analysis", {})
                                    risk_level = analysis.get("risk_level", "Unknown").upper()
                                    
                                    # Header indicators
                                    if risk_level == "HIGH":
                                        st.error(f"⚠️ Risk Level: {risk_level}")
                                    elif risk_level == "MEDIUM":
                                        st.warning(f"⚠️ Risk Level: {risk_level}")
                                    else:
                                        st.success(f"✅ Risk Level: {risk_level}")
                                    
                                    st.markdown("#### Detected Risks & Guidance")
                                    for item in analysis.get("risks", []):
                                        severity = item.get("severity", "medium").upper()
                                        color = "red" if severity == "HIGH" else "orange"
                                        st.markdown(
                                            f"<div style='border-left: 5px solid {color}; padding-left: 10px; margin-bottom: 15px;'>"
                                            f"<strong>Risk: {item.get('risk')}</strong> ({severity})<br/>"
                                            f"<em>Text flagged:</em> \"{item.get('matched_text')}\"<br/>"
                                            f"<strong>Guidance:</strong> {item.get('guidance')}"
                                            f"</div>", 
                                            unsafe_allow_html=True
                                        )
                                    
                                    if not analysis.get("risks"):
                                        st.info("No major liability risks detected based on standard patterns.")
                                else:
                                    st.error(f"Analysis failed: {res.text}")
                            except Exception as e:
                                st.error(f"Error: {e}")

            with sub_calendar:
                st.markdown("### 📅 Corporate Compliance Calendar")
                st.markdown("Generate filing deadlines for Statement of Information with the Secretary of State.")
                
                cal_entity = st.selectbox("Entity Structure", ["llc", "corporation"])
                cal_date = st.date_input("Inception/Registration Date", value=datetime.date.today())
                
                if st.button("Generate Compliance Deadlines"):
                    payload = {"entity_type": cal_entity, "inception_date": cal_date.strftime("%Y-%m-%d")}
                    try:
                        res = requests.post(f"{BACKEND_URL}/api/business-pro/tools/compliance-calendar", json=payload, headers=get_headers())
                        if res.status_code == 200:
                            calendar_items = res.json().get("calendar", [])
                            if calendar_items:
                                st.markdown("#### filing deadlines")
                                for item in calendar_items:
                                    st.info(
                                        f"📅 **{item.get('filing')}**\n"
                                        f"- **Filing Window:** {item.get('window_start')} to {item.get('deadline')}\n"
                                        f"- **Status:** {item.get('status')}\n"
                                        f"- **Days Until Deadline:** {item.get('days_until')} days"
                                    )
                            else:
                                st.info("No upcoming deadlines found.")
                        else:
                            st.error(f"Failed to generate calendar: {res.text}")
                    except Exception as e:
                        st.error(f"Error: {e}")

            with sub_hr:
                st.markdown("### 👥 HR Onboarding & Compliance Advisor")
                st.markdown("Generate compliant onboarding check-lists for California employment regulations.")
                
                emp_type = st.selectbox("Employee Classification", ["non_exempt", "exempt"])
                if st.button("Generate Onboarding Checklist"):
                    payload = {"employee_type": emp_type}
                    try:
                        res = requests.post(f"{BACKEND_URL}/api/business-pro/tools/new-hire-checklist", json=payload, headers=get_headers())
                        if res.status_code == 200:
                            checklist = res.json().get("checklist", {})
                            
                            col1, col2 = st.columns(2)
                            with col1:
                                st.markdown("#### Day One Requirements")
                                for item in checklist.get("day_one", []):
                                    st.write(f"- [ ] {item}")
                            with col2:
                                st.markdown("#### First Week Compliance")
                                for item in checklist.get("first_week", []):
                                    st.write(f"- [ ] {item}")
                                    
                            st.markdown("#### Post-Hire Checklist")
                            for item in checklist.get("post_hire", []):
                                st.write(f"- [ ] {item}")
                        else:
                            st.error(f"Checklist generation failed: {res.text}")
                    except Exception as e:
                        st.error(f"Error: {e}")

            with sub_dispute:
                st.markdown("### ⚖️ dispute Strategist & Collections")
                st.markdown("Formulate invoice recovery strategies and draft formal demand letters.")
                
                col1, col2 = st.columns(2)
                with col1:
                    col_amount = st.number_input("Invoice Amount ($)", min_value=0.0, value=5000.0)
                    col_type = st.selectbox("Agreement Type", ["written_contract", "oral_agreement", "purchase_order", "none"])
                with col2:
                    col_days = st.number_input("Days Past Due", min_value=0, value=60)
                
                if st.button("Formulate Dispute Strategy"):
                    payload = {"amount": col_amount, "agreement_type": col_type, "delinquency_days": col_days}
                    try:
                        res = requests.post(f"{BACKEND_URL}/api/business-pro/tools/collection-strategy", json=payload, headers=get_headers())
                        if res.status_code == 200:
                            strategy = res.json().get("strategy", {})
                            st.success(f"Recommended Venue: **{strategy.get('recommended_venue')}**")
                            
                            st.markdown("#### Recommended Recovery Steps")
                            for step in strategy.get("steps", []):
                                st.info(f"👉 {step}")
                        else:
                            st.error(f"Strategy retrieval failed: {res.text}")
                    except Exception as e:
                        st.error(f"Error: {e}")
                
                st.divider()
                st.markdown("#### 📜 Draft Demand Letter")
                with st.form("demand_letter_form"):
                    debtor_name = st.text_input("Debtor Name (Client/Vendor)", value="Acme Corp")
                    creditor_name = st.text_input("Creditor Name (Your Business)", value=company_name or "My Business LLC")
                    invoice_date = st.text_input("Original Invoice Date", value="2026-05-15")
                    desc = st.text_area("Description of services/goods", value="Outstanding software development services.")
                    
                    draft_submitted = st.form_submit_button("Generate Demand Letter")
                    if draft_submitted:
                        letter_payload = {
                            "creditor": creditor_name,
                            "debtor": debtor_name,
                            "amount": col_amount,
                            "invoice_date": invoice_date,
                            "description": desc
                        }
                        try:
                            res = requests.post(f"{BACKEND_URL}/api/business-pro/tools/demand-letter", json=letter_payload, headers=get_headers())
                            if res.status_code == 200:
                                letter_text = res.json().get("letter_text")
                                st.text_area("Generated Demand Letter Template", value=letter_text, height=350)
                                st.info("💡 You can copy this letter text and paste it into your local document template.")
                            else:
                                st.error(f"Generation failed: {res.text}")
                        except Exception as e:
                            st.error(f"Error: {e}")

            with sub_employee:
                st.markdown("### 🧑‍💻 Individual Contributor Workflow Suite")
                st.markdown("Optimize daily work, extract templates, and parse receipts using standard internal precedents.")
                
                emp_tool = st.selectbox(
                    "Select Employee Workflow Tool", 
                    ["📋 Daily briefing", "✅ SOP Checklist Generator", "📖 Code Snippet Companion", "📸 Local Vision OCR (Receipts)"]
                )
                
                if emp_tool == "📋 Daily briefing":
                    st.markdown("#### 📋 Daily Activity Briefing")
                    lookback_hours = st.slider("Lookback Interval (Hours)", min_value=1, max_value=168, value=24)
                    if st.button("Generate Briefing", key="btn_gen_briefing"):
                        with st.spinner("Compiling briefing..."):
                            try:
                                payload = {"workspace_id": selected_workspace_id, "lookback_hours": lookback_hours}
                                res = requests.post(f"{BACKEND_URL}/api/employee/briefing", json=payload, headers=get_headers())
                                if res.status_code == 200:
                                    briefing = res.json().get("briefing")
                                    st.markdown(briefing)
                                else:
                                    st.error(f"Failed: {res.text}")
                            except Exception as e:
                                st.error(f"Error: {e}")
                                
                elif emp_tool == "✅ SOP Checklist Generator":
                    st.markdown("#### ✅ SOP Checklist Generator")
                    st.markdown("Convert manual instructions in the workspace into structured action items.")
                    sop_query = st.text_input("Procedure Name (e.g. deploy hotfix, request permissions)", value="deploy hotfix")
                    if st.button("Generate Action Checklist", key="btn_gen_sop"):
                        with st.spinner("Extracting procedure steps..."):
                            try:
                                payload = {"workspace_id": selected_workspace_id, "query": sop_query}
                                res = requests.post(f"{BACKEND_URL}/api/employee/sop-checklist", json=payload, headers=get_headers())
                                if res.status_code == 200:
                                    checklist = res.json().get("checklist", {})
                                    st.subheader(checklist.get("title", f"Checklist: {sop_query}"))
                                    for phase in checklist.get("phases", []):
                                        st.markdown(f"**{phase.get('name')}**")
                                        for task in phase.get("tasks", []):
                                            st.checkbox(task, key=f"chk_{hash(task)}")
                                else:
                                    st.error(f"Failed: {res.text}")
                            except Exception as e:
                                st.error(f"Error: {e}")
                                
                elif emp_tool == "📖 Code Snippet Companion":
                    st.markdown("#### 📖 Code Snippet Companion")
                    st.markdown("Retrieve codebase precedents and syntax guidelines from ingested repositories.")
                    code_query = st.text_input("Snippet Query (e.g. database session, sse connection)", value="database session")
                    if st.button("Fetch Syntax Template", key="btn_gen_snippet"):
                        with st.spinner("Searching repository code files..."):
                            try:
                                payload = {"workspace_id": selected_workspace_id, "query": code_query}
                                res = requests.post(f"{BACKEND_URL}/api/employee/snippet-companion", json=payload, headers=get_headers())
                                if res.status_code == 200:
                                    snippet = res.json().get("snippet", {})
                                    st.markdown(f"##### {snippet.get('title')}")
                                    st.code(snippet.get("code"), language=snippet.get("language", "python"))
                                    st.markdown(f"**Explanation:** {snippet.get('explanation')}")
                                else:
                                    st.error(f"Failed: {res.text}")
                            except Exception as e:
                                st.error(f"Error: {e}")
                                
                elif emp_tool == "📸 Local Vision OCR (Receipts)":
                    st.markdown("#### 📸 Local Vision OCR & Expense Drafter")
                    st.markdown("Scan receipts and invoices locally using local Ollama multimodal vision models (llama3.2-vision).")
                    
                    ocr_file = st.file_uploader("Upload Receipt Image", type=["png", "jpg", "jpeg", "webp"], key="employee_ocr_uploader")
                    if ocr_file is not None:
                        st.image(ocr_file, caption="Uploaded Receipt", use_container_width=True)
                        if st.button("Run Local OCR Scan", key="btn_run_ocr"):
                            with st.spinner("Ollama Vision analyzing receipt..."):
                                try:
                                    files = {"file": (ocr_file.name, ocr_file.getvalue(), ocr_file.type)}
                                    res = requests.post(f"{BACKEND_URL}/api/employee/receipt-ocr", files=files, headers=get_headers())
                                    if res.status_code == 200:
                                        ocr_data = res.json().get("ocr_data", {})
                                        st.success(f"Successfully processed by local vision model!")
                                        
                                        st.markdown("### 📝 Drafted Expense Report")
                                        st.metric("Total Amount", f"{ocr_data.get('total')} {ocr_data.get('currency', 'USD')}")
                                        st.write(f"**Merchant:** {ocr_data.get('merchant')}")
                                        st.write(f"**Date:** {ocr_data.get('date')}")
                                        st.write(f"**Category:** `{ocr_data.get('classification')}`")
                                        st.write(f"**Model Confidence:** `{ocr_data.get('confidence') * 100}%`")
                                        
                                        st.write("**Line Items:**")
                                        for item in ocr_data.get("items", []):
                                            st.write(f"- {item.get('name')}: ${item.get('price')}")
                                            
                                        if "note" in ocr_data:
                                            st.info(f"ℹ️ {ocr_data.get('note')}")
                                    else:
                                        st.error(f"OCR scan failed: {res.text}")
                                except Exception as e:
                                    st.error(f"Error: {e}")

    if tab_alignment is not None:
        with tab_alignment:
            st.subheader("🎯 Alignment & Post-Training Hub (PUST / Direct-OPD)")
        st.markdown(
            "Track background proxy exploration audit warning signals, human feedback, and compile datasets for offline instruction tuning."
        )

        try:
            res = requests.get(f"{BACKEND_URL}/api/alignment/signals", headers=get_headers())
            if res.status_code == 200:
                signals = res.json()
                
                if not signals:
                    st.info("No alignment signal feedback records found in kruschdb. Trigger proactive audits to start collecting alignment traces.")
                else:
                    # Calculate stats
                    total_signals = len(signals)
                    approved_signals = sum(1 for s in signals if s.get("user_approved"))
                    corrected_signals = sum(1 for s in signals if s.get("agent_corrected"))
                    approval_rate = (approved_signals / total_signals * 100) if total_signals > 0 else 0
                    correction_rate = (corrected_signals / total_signals * 100) if total_signals > 0 else 0

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Total Proxy Audits Logged", total_signals)
                    with col2:
                        st.metric("Developer Approval Rate", f"{approval_rate:.1f}%")
                    with col3:
                        st.metric("Trajectory Correction Rate", f"{correction_rate:.1f}%")

                    st.markdown("### 💾 Export Alignment Dataset")
                    st.write("Export verified alignment signal traces into a JSONL format suitable for local model post-training (PUST).")
                    
                    if st.button("Export PUST JSONL Dataset", key="btn_export_pust"):
                        with st.spinner("Generating dataset..."):
                            dataset_dir = os.getenv("AI_WATCH_DATASET_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "data", "datasets"))
                            os.makedirs(dataset_dir, exist_ok=True)
                            dataset_path = os.path.join(dataset_dir, "pust_alignment_dataset.jsonl")
                            
                            count = 0
                            with open(dataset_path, "w", encoding="utf-8") as f:
                                for s in signals:
                                    if s.get("user_approved") and s.get("agent_corrected"):
                                        # Format as instruction/input/output Alpaca-style
                                        item = {
                                            "instruction": "Audit the proposed agent trajectory query and output any warning nudge alert showing risk violations.",
                                            "input": s.get("query_text", ""),
                                            "output": s.get("nudge_text") + ("\n\nCorrection Diff:\n" + s.get("correction_diff") if s.get("correction_diff") else "")
                                        }
                                        f.write(json.dumps(item) + "\n")
                                        count += 1
                                        
                            st.success(f"🎉 Successfully exported {count} alignment exemplars to: {dataset_path}")

                    st.markdown("### 📝 Logged Alignment Traces")
                    for sig in signals:
                        status_emoji = "✅" if sig.get("user_approved") and sig.get("agent_corrected") else "⚠️"
                        with st.expander(f"{status_emoji} Query: {sig.get('query_text')[:60]}... ({sig.get('project') or 'Global'})"):
                            st.markdown(f"**Query:** {sig.get('query_text')}")
                            st.markdown(f"**Nudge/Warning Alert:**\n{sig.get('nudge_text')}")
                            
                            col_a, col_b = st.columns(2)
                            with col_a:
                                st.write(f"**User Approved:** `{'Yes' if sig.get('user_approved') else 'No'}`")
                            with col_b:
                                st.write(f"**Agent Corrected:** `{'Yes' if sig.get('agent_corrected') else 'No'}`")
                                
                            if sig.get("correction_diff"):
                                st.markdown("**Correction Diff:**")
                                st.code(sig.get("correction_diff"), language="diff")
                            
                            st.caption(f"Logged at: {sig.get('created_at')} | ID: `{sig.get('id')}`")
            else:
                st.error(f"Failed to fetch alignment signals: {res.text}")
        except Exception as e:
            st.error(f"Error loading Alignment Hub: {e}")

else:
    if st.session_state.role == "admin":
        st.info("👈 Create a workspace in the sidebar to get started.")
    else:
        st.info("No workspaces available. Please contact an admin.")
