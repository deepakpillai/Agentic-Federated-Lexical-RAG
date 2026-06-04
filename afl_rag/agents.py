import os
import json
from openai import AzureOpenAI
from pydantic import BaseModel, Field
from typing import List, Optional

class HyDEResponse(BaseModel):
    keywords: List[str] = Field(description="Top 10 most critical domain-specific search keywords/synonyms.")
    kql_query: str = Field(description="A boolean KQL query string using the keywords (e.g., ('keyword1' OR 'keyword2') AND 'keyword3'). Do not include path or site restrictions.")

class CriticResponse(BaseModel):
    is_complete: bool = Field(description="True if the answer fully addresses the user prompt, False otherwise.")
    feedback: Optional[str] = Field(description="If incomplete, specific instructions on what is missing and what to search for next.")

class Agents:
    def __init__(self):
        self.client = AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
        )
        self.deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")

    def lexical_hyde_and_extract(self, user_prompt: str, previous_feedback: str = None) -> HyDEResponse:
        """
        Agent 1 / Setup: Generates a hypothetical answer, then extracts keywords and KQL.
        Uses structured output to guarantee JSON formatting.
        """
        system_prompt = (
            "You are an expert enterprise search architect. "
            "Your task is to help formulate a keyword search query to find documents answering the user's prompt.\n"
            "Step 1: Write a highly detailed, hypothetical answer to the user's question, using professional domain jargon. "
            "Step 2: Analyze your hypothetical answer and extract the most critical keywords.\n"
            "Step 3: Construct a SharePoint KQL (Keyword Query Language) boolean query string from these keywords.\n"
            "CRITICAL KQL RULES:\n"
            "- Native Metadata Injection: If the user prompt mentions specific people, dates, or file types, extract them as hard constraints using KQL properties (e.g., Author:\"John Doe\" OR LastModifiedTime>2023-01-01).\n"
            "- Length Limit: The final KQL query MUST be under 4,096 characters. Prioritize only the most valuable synonyms.\n"
        )
        
        if previous_feedback:
            system_prompt += f"\nNote: A previous search failed. Please adjust your keywords based on this feedback: '{previous_feedback}'"

        response = self.client.chat.completions.create(
            model=self.deployment_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "output_search_parameters",
                        "description": "Outputs the extracted keywords and KQL query.",
                        "parameters": HyDEResponse.schema()
                    }
                }
            ],
            tool_choice={"type": "function", "function": {"name": "output_search_parameters"}}
        )

        tool_call = response.choices[0].message.tool_calls[0]
        arguments = json.loads(tool_call.function.arguments)
        return HyDEResponse(**arguments)

    def assess_snippets(self, user_prompt: str, snippets: List[dict]) -> str:
        """
        Agent 1 (The Scout): Reviews snippets to see if any are promising enough to double-tap.
        Returns the Document ID to deep dive, or None if all snippets are irrelevant.
        """
        if not snippets:
            return None
            
        system_prompt = (
            "You are a Scout Agent. You are given a list of search snippets. "
            "Determine if any snippet contains information relevant to the user's prompt. "
            "If yes, return ONLY the ID of the most relevant document. If no, return exactly 'NONE'."
        )
        
        snippets_text = "\n".join([f"ID: {s['id']} | Summary: {s['summary']}" for s in snippets])
        user_content = f"User Prompt: {user_prompt}\n\nSnippets:\n{snippets_text}"

        response = self.client.chat.completions.create(
            model=self.deployment_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.0
        )
        
        result = response.choices[0].message.content.strip()
        return None if result == 'NONE' else result

    def _chunk_and_filter(self, user_prompt: str, document_content: str, max_tokens: int = 15000) -> str:
        """
        Simulates 'in-memory chunking' as per the Vectorless RAG architecture.
        Splits by paragraph and performs a basic keyword overlap to find the most relevant chunks 
        so we don't blow out the context window with massive irrelevant PDFs.
        """
        # Very basic keyword extraction for scoring (in a real app, use NLTK or similar)
        keywords = set([w.lower() for w in user_prompt.split() if len(w) > 3])
        
        paragraphs = document_content.split('\n\n')
        scored_paragraphs = []
        
        for p in paragraphs:
            score = sum(1 for kw in keywords if kw in p.lower())
            scored_paragraphs.append((score, p))
            
        # Sort by highest keyword overlap
        scored_paragraphs.sort(key=lambda x: x[0], reverse=True)
        
        # Reassemble the top chunks until we hit a safe limit
        filtered_content = ""
        for score, p in scored_paragraphs:
            if len(filtered_content) + len(p) > max_tokens:
                break
            filtered_content += p + "\n\n"
            
        # If the document is small, or keywords failed, just return the top part
        if not filtered_content:
            return document_content[:max_tokens]
            
        return filtered_content

    def deep_dive_generation(self, user_prompt: str, document_content: str) -> str:
        """
        Agent 2 (The Deep Diver): Reads the full document content and generates the answer.
        """
        system_prompt = (
            "You are the Deep Dive Agent. Read the provided document content and answer the user's prompt. "
            "If the document does not contain the answer, say so. Cite specific information where possible."
        )

        # Execute in-memory chunking to isolate paragraphs surrounding the hit
        filtered_content = self._chunk_and_filter(user_prompt, document_content)

        response = self.client.chat.completions.create(
            model=self.deployment_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"User Prompt: {user_prompt}\n\nDocument Content:\n{filtered_content}"}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content

    def critic_review(self, user_prompt: str, generated_answer: str) -> CriticResponse:
        """
        Agent 3 (The Critic): Checks if the generated answer fully satisfies the prompt.
        """
        system_prompt = (
            "You are the Critic Agent. Review the generated answer against the user's prompt. "
            "Did the answer fully satisfy the prompt? If not, what specific information is missing?"
        )

        response = self.client.chat.completions.create(
            model=self.deployment_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Prompt: {user_prompt}\n\nAnswer: {generated_answer}"}
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "output_critique",
                        "description": "Outputs the critique results.",
                        "parameters": CriticResponse.schema()
                    }
                }
            ],
            tool_choice={"type": "function", "function": {"name": "output_critique"}}
        )

        tool_call = response.choices[0].message.tool_calls[0]
        arguments = json.loads(tool_call.function.arguments)
        return CriticResponse(**arguments)
