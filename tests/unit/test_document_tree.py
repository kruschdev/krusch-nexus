"""
Unit tests for PageIndex-style Hierarchical Document Tree in KruschNexus
(test_document_tree.py)
"""

import json
import pytest
from unittest.mock import MagicMock

from krusch_nexus.models import (
    NexusConfig,
    TreeNode,
    DocumentTree,
    DocType
)
from krusch_nexus.client import NexusClient
from krusch_nexus.exceptions import DocumentNotFound, WorkspaceNotFound
from krusch_nexus.mcp import nexus_get_document_tree, set_client


class TestDocumentTree:
    """Test suite for hierarchical Table of Contents document trees."""

    def test_tree_models(self):
        """Verify TreeNode and DocumentTree Pydantic schema validation."""
        child = TreeNode(
            title="Section 1.1 Scope",
            level=2,
            page=1,
            chunk_id=2,
            chunk_index=1,
            locator="Page 1",
            children=[]
        )
        parent = TreeNode(
            title="Article I: General Provisions",
            level=1,
            page=1,
            chunk_id=1,
            chunk_index=0,
            locator="Page 1",
            children=[child]
        )
        tree = DocumentTree(
            document_id=10,
            filename="master_agreement.pdf",
            workspace="Corporate",
            total_chunks=2,
            total_pages=1,
            tree=[parent]
        )

        assert tree.document_id == 10
        assert len(tree.tree) == 1
        assert tree.tree[0].title == "Article I: General Provisions"
        assert len(tree.tree[0].children) == 1
        assert tree.tree[0].children[0].title == "Section 1.1 Scope"
        assert tree.tree[0].children[0].level == 2

    def test_tree_generation_on_demo_fixture(self):
        """Verify tree assembly on static SQLite fixture (data/demo.db)."""
        cfg = NexusConfig(
            database_url="sqlite:///./data/demo.db",
            allow_cloud=False
        )
        client = NexusClient(config=cfg)

        tree_by_id = client.get_document_tree(1, workspace="LegalCorpus")
        assert isinstance(tree_by_id, DocumentTree)
        assert tree_by_id.filename == "msa_commercial.txt"
        assert tree_by_id.total_chunks == 7
        assert len(tree_by_id.tree) == 3

        # Level 1 nodes
        titles = [n.title for n in tree_by_id.tree]
        assert "ARTICLE I: RECITALS" in titles
        assert "ARTICLE IV: FINANCIAL TERMS" in titles
        assert "ARTICLE IX: LIMITATION OF LIABILITY" in titles

        # Verify nesting under ARTICLE IV
        art_iv = next(n for n in tree_by_id.tree if "FINANCIAL" in n.title)
        assert len(art_iv.children) == 2
        assert art_iv.children[0].title == "Section 4.1 Invoicing"
        assert art_iv.children[1].title == "Section 4.2 Payment Terms"

        # Verify nesting under ARTICLE IX
        art_ix = next(n for n in tree_by_id.tree if "LIMITATION" in n.title)
        assert len(art_ix.children) == 2
        assert art_ix.children[0].title == "Section 9.1 Aggregate Cap"
        assert art_ix.children[1].title == "Section 9.2 Consequential Damages Waiver"

        # Verify resolution by filename
        tree_by_name = client.get_document_tree("msa_commercial.txt", workspace="LegalCorpus")
        assert tree_by_name.document_id == tree_by_id.document_id
        assert len(tree_by_name.tree) == len(tree_by_id.tree)

    def test_tree_document_not_found(self):
        """Verify proper DocumentNotFound exception on non-existent document."""
        cfg = NexusConfig(database_url="sqlite:///./data/demo.db", allow_cloud=False)
        client = NexusClient(config=cfg)

        with pytest.raises(DocumentNotFound, match="Document '99999' not found"):
            client.get_document_tree(99999, workspace="LegalCorpus")

        with pytest.raises(DocumentNotFound, match="Document 'ghost.pdf' not found"):
            client.get_document_tree("ghost.pdf", workspace="LegalCorpus")

    def test_tree_workspace_not_found(self):
        """Verify WorkspaceNotFound when given invalid workspace."""
        cfg = NexusConfig(database_url="sqlite:///./data/demo.db", allow_cloud=False)
        client = NexusClient(config=cfg)

        with pytest.raises(WorkspaceNotFound, match="Workspace 'NonExistentWS' not found"):
            client.get_document_tree(1, workspace="NonExistentWS")

    def test_tree_mcp_tool_execution(self):
        """Verify nexus_get_document_tree MCP tool execution."""
        cfg = NexusConfig(database_url="sqlite:///./data/demo.db", allow_cloud=False)
        client = NexusClient(config=cfg)
        set_client(client)

        resp_raw = nexus_get_document_tree("1", workspace="LegalCorpus")
        resp = json.loads(resp_raw)

        assert resp["status"] == "success"
        doc_tree = resp["document_tree"]
        assert doc_tree["filename"] == "msa_commercial.txt"
        assert len(doc_tree["tree"]) == 3
