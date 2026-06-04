from typing import Tuple, List, Dict, Any
from .agents import Agents
from .graph_client import GraphClient

class Orchestrator:
    def __init__(self, update_callback=None):
        self.agents = Agents()
        self.graph = GraphClient()
        self.update_callback = update_callback or (lambda msg: print(f"[Status] {msg}"))

    def _log(self, msg: str):
        self.update_callback(msg)

    def run(self, user_prompt: str) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Executes the full AFL-RAG pipeline.
        Returns: (final_answer, raw_snippets_list)
        """
        max_iterations = 5
        iteration = 0
        feedback = None
        all_retrieved_snippets = []
        final_answer = ""

        while iteration < max_iterations:
            iteration += 1
            self._log(f"--- Iteration {iteration}/{max_iterations} ---")
            
            # 1. Lexical HyDE & KQL Generation
            self._log("Agent 1: Generating HyDE response and extracting KQL...")
            hyde_res = self.agents.lexical_hyde_and_extract(user_prompt, feedback)
            self._log(f"Agent 1 KQL: {hyde_res.kql_query}")

            # 2. Scout Search (Graph API)
            self._log(f"Graph API: Searching SharePoint for snippets...")
            snippets = self.graph.search_sharepoint(hyde_res.kql_query)
            all_retrieved_snippets.extend(snippets)
            
            if not snippets:
                self._log("Graph API: No results found for this query.")
                feedback = f"The query '{hyde_res.kql_query}' returned no results. Try entirely different synonyms."
                continue

            # 3. Assess Snippets (Agent 1)
            self._log("Agent 1: Assessing snippets for relevance...")
            document_id = self.agents.assess_snippets(user_prompt, snippets)
            
            if not document_id:
                self._log("Agent 1: Snippets are irrelevant. Refining search...")
                feedback = "The retrieved snippets were not relevant to the user's question. Try refining the search terms."
                continue

            # 4. Deep Dive Double-Tap (Agent 2)
            # Find the chosen snippet to get its driveId
            chosen_snippet = next((s for s in snippets if s['id'] == document_id), None)
            drive_id = chosen_snippet.get('driveId') if chosen_snippet else None

            self._log(f"Graph API: Double-Tapping! Downloading document ID: {document_id}")
            if drive_id and document_id:
                document_content = self.graph.download_document(drive_id, document_id)
            else:
                self._log("Graph API Error: Missing driveId or document_id. Cannot download document.")
                feedback = "Failed to download the specific document due to missing Drive ID. Try refining the search."
                continue
            
            self._log("Agent 2: Deep diving into document content and generating answer...")
            draft_answer = self.agents.deep_dive_generation(user_prompt, document_content)

            # 5. Critic Review (Agent 3)
            self._log("Agent 3: Critiquing the generated answer...")
            critique = self.agents.critic_review(user_prompt, draft_answer)

            if critique.is_complete:
                self._log("Agent 3: Answer is complete and satisfactory.")
                final_answer = draft_answer
                break
            else:
                self._log(f"Agent 3: Answer is incomplete. Feedback: {critique.feedback}")
                feedback = critique.feedback
                # Optional: Append the draft answer to the final answer if we want to build it up, 
                # but for this POC we will just let it loop and refine.

        if not final_answer:
            final_answer = "I was unable to find a complete answer to your query after maximum search iterations."

        return final_answer, all_retrieved_snippets
