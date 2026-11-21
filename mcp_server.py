"""Run with a trusted local MCP client: python mcp_server.py."""
import argparse
from dotenv import load_dotenv
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--env-file', help='Path to a private environment file; never put secrets in MCP client config')
if __name__ == '__main__':
    args = parser.parse_args()
    load_dotenv(args.env_file)
else:
    load_dotenv()
from mcp.server.fastmcp import FastMCP
from mcp_publication import prepare_publication, publish_record
mcp = FastMCP("Civic Lens")
mcp.tool()(prepare_publication)
mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False})(publish_record)

@mcp.tool(annotations={"readOnlyHint": True})
def get_publication_status(uid_or_record_id: str) -> dict:
    """Read a publication from the chain; this does not prove submission to an authority."""
    from web3_attestation import get_attestation
    record = get_attestation(uid_or_record_id)
    return {"found": record is not None, "attestation": record.model_dump() if record else None}

@mcp.tool(annotations={"readOnlyHint": True})
def verify_publication(uid_or_record_id: str, content: str) -> dict:
    """Compare an original document hash against its on-chain publication."""
    from web3_attestation import verify_attestation
    return verify_attestation(uid_or_record_id, content)

if __name__ == "__main__":
    mcp.run(transport="stdio")
