import streamlit as st
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from afl_rag.orchestrator import Orchestrator

# Configure Streamlit page
st.set_page_config(
    page_title="AFL-RAG Enterprise Search",
    page_icon="🔍",
    layout="centered"
)

# Custom CSS for a "Google-style" search experience
st.markdown("""
<style>
    .main .block-container {
        padding-top: 2rem;
    }
    .search-title {
        text-align: center;
        font-family: 'Inter', sans-serif;
        color: #2e3b4e;
        margin-bottom: -1rem;
    }
    .stTextInput input {
        border-radius: 24px;
        border: 1px solid #dfe1e5;
        padding: 10px 20px;
        box-shadow: 0 1px 6px rgba(32,33,36,.28);
        font-size: 16px;
    }
    .stTextInput input:hover {
        box-shadow: 0 1px 6px rgba(32,33,36,.28);
        border-color: rgba(223,225,229,0);
    }
    .answer-box {
        background-color: #f8f9fa;
        border-radius: 8px;
        padding: 20px;
        margin-top: 20px;
        border-left: 4px solid #1a73e8;
    }
    .snippet-box {
        padding: 10px;
        border-bottom: 1px solid #eee;
    }
    .snippet-title {
        color: #1a0dab;
        font-size: 18px;
        font-weight: 500;
        text-decoration: none;
    }
    .snippet-url {
        color: #006621;
        font-size: 14px;
        margin-bottom: 4px;
    }
</style>
""", unsafe_allow_html=True)

st.markdown("<h1 class='search-title'>AFL-RAG Search</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align: center; color: #666;'>Agentic Federated Lexical RAG</p>", unsafe_allow_html=True)

# Search Bar
query = st.text_input("Search SharePoint...", placeholder="What did John Daniel do on the EV project?", label_visibility="collapsed")

if query:
    status_container = st.container()
    
    with status_container:
        with st.status("Executing AFL-RAG Pipeline...", expanded=True) as status:
            
            # Callback to update UI with agent status
            def update_ui(msg):
                st.write(msg)
                
            orchestrator = Orchestrator(update_callback=update_ui)
            
            try:
                final_answer, snippets = orchestrator.run(query)
                status.update(label="Search Complete!", state="complete", expanded=False)
            except Exception as e:
                status.update(label=f"Error: {str(e)}", state="error", expanded=True)
                st.stop()

    # Display Final Answer
    st.markdown("### Generated Answer")
    st.markdown(f"<div class='answer-box'>{final_answer}</div>", unsafe_allow_html=True)
    
    st.divider()
    
    # Display Raw SharePoint Data
    st.markdown("### Raw SharePoint Search Results")
    st.caption("These are the lexical snippets retrieved by the Scout Agent.")
    
    if snippets:
        # Deduplicate snippets by ID for display
        seen_ids = set()
        unique_snippets = []
        for s in snippets:
            if s['id'] not in seen_ids:
                unique_snippets.append(s)
                seen_ids.add(s['id'])
                
        for snippet in unique_snippets:
            url = snippet.get('url', '#')
            name = snippet.get('name', 'Unknown Document')
            summary = snippet.get('summary', 'No summary available.')
            
            st.markdown(f"""
            <div class='snippet-box'>
                <div class='snippet-url'>{url}</div>
                <a href='{url}' target='_blank' class='snippet-title'>{name}</a>
                <p>{summary}</p>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No raw snippets were retrieved.")
