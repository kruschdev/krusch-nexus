import pytest
from krusch_nexus.client import NexusClient


def test_import_workspace_rejects_empty_or_nonexistent_tarball(tmp_path):
    client = NexusClient.__new__(NexusClient)

    with pytest.raises(ValueError, match="tarball_path must be a non-empty string"):
        client.import_workspace("")

    with pytest.raises(ValueError, match="tarball_path must be a non-empty string"):
        client.import_workspace("   ")

    missing = tmp_path / "missing_bundle.tar.gz"
    with pytest.raises(ValueError, match="tarball_path does not exist"):
        client.import_workspace(str(missing))


def test_import_workspace_rejects_empty_target_workspace(tmp_path):
    client = NexusClient.__new__(NexusClient)
    dummy_tar = tmp_path / "dummy.tar.gz"
    dummy_tar.write_bytes(b"dummy")

    with pytest.raises(ValueError, match="target_workspace must be a non-empty string"):
        client.import_workspace(str(dummy_tar), target_workspace="")

    with pytest.raises(ValueError, match="target_workspace must be a non-empty string"):
        client.import_workspace(str(dummy_tar), target_workspace="   ")
