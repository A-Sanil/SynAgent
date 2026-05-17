import httpx
from typing import Optional, List
from pydantic import BaseModel

# ========================
# Data Models
# ========================


class PubChemHazardSummary(BaseModel):
    """Structured hazard information extracted from PubChem."""
    smiles: str
    cid: Optional[int] = None
    iupac_name: Optional[str] = None
    ghs_classification: List[str] = []
    signal_word: Optional[str] = None
    hazard_statements: List[str] = []
    precautionary_statements: List[str] = []
    pictograms: List[str] = []
    red_flag: bool = False
    found: bool = False


# ========================
# PubChem API Helpers
# ========================


async def smiles_to_cid(smiles: str) -> Optional[int]:
    """
    Convert SMILES string to PubChem Compound ID (CID).
    
    Args:
        smiles: SMILES string
        
    Returns:
        CID if found, None otherwise
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/cids/JSON",
                params={"smiles": smiles},
            )
            response.raise_for_status()
            data = response.json()
            if "IdentifierList" in data and "CID" in data["IdentifierList"]:
                cids = data["IdentifierList"]["CID"]
                return cids[0] if cids else None
    except Exception as e:
        print(f"Error converting SMILES to CID: {e}")
    return None


async def get_compound_record(cid: int) -> dict:
    """
    Fetch full compound record from PubChem by CID.
    
    Args:
        cid: PubChem Compound ID
        
    Returns:
        Compound record JSON
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/CID/{cid}/JSON",
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        print(f"Error fetching compound record for CID {cid}: {e}")
    return {}


async def get_ghs_hazard_data(cid: int) -> dict:
    """
    Fetch GHS hazard classification from PubChem using PUG View.
    
    Uses the NIH GHS endpoint to retrieve Laboratory Chemical Safety Summary (LCSS).
    
    Args:
        cid: PubChem Compound ID
        
    Returns:
        GHS/hazard classification data
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Use PUG View to get GHS data
            response = await client.get(
                f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON?heading=GHS%20Classification",
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        print(f"Error fetching GHS data for CID {cid}: {e}")
    return {}


def extract_hazard_codes_from_ghs(ghs_data: dict) -> List[str]:
    """
    Parse GHS data to extract hazard codes (H-codes).
    
    Args:
        ghs_data: GHS classification data from PubChem
        
    Returns:
        List of H-codes (e.g., ["H300", "H315"])
    """
    hazard_codes = []
    
    try:
        # Navigate PubChem JSON structure to find hazard statements
        if "Record" in ghs_data:
            record = ghs_data["Record"]
            if "Section" in record:
                for section in record["Section"]:
                    if section.get("TOCHeading") == "GHS Classification":
                        if "Information" in section:
                            for info in section["Information"]:
                                if "Value" in info:
                                    value = info["Value"]
                                    # Extract H-codes from text
                                    if isinstance(value, dict) and "StringWithMarkup" in value:
                                        for item in value["StringWithMarkup"]:
                                            text = item.get("String", "")
                                            # Parse H-codes like H300, H315, etc.
                                            import re
                                            codes = re.findall(r"H\d{3}", text)
                                            hazard_codes.extend(codes)
    except Exception as e:
        print(f"Error extracting hazard codes: {e}")
    
    return list(set(hazard_codes))  # Remove duplicates


async def get_hazard_summary(smiles: str) -> PubChemHazardSummary:
    """
    Fetch complete hazard summary for a compound by SMILES.
    
    This is the main entry point: it handles SMILES->CID resolution,
    fetches GHS data, and returns a structured summary.
    
    Args:
        smiles: SMILES string
        
    Returns:
        PubChemHazardSummary with all extracted data
    """
    summary = PubChemHazardSummary(smiles=smiles, found=False)
    
    # Step 1: Resolve SMILES to CID
    cid = await smiles_to_cid(smiles)
    if not cid:
        return summary
    
    summary.cid = cid
    
    # Step 2: Fetch compound record for basic info
    record = await get_compound_record(cid)
    if record and "PC_Compounds" in record and len(record["PC_Compounds"]) > 0:
        compound = record["PC_Compounds"][0]
        summary.found = True
        
        # Extract IUPAC name if available
        if "props" in compound:
            for prop in compound["props"]:
                if prop.get("urn", {}).get("label") == "IUPAC Name":
                    summary.iupac_name = prop.get("value", {}).get("sval")
    
    # Step 3: Fetch GHS hazard data
    ghs_data = await get_ghs_hazard_data(cid)
    hazard_codes = extract_hazard_codes_from_ghs(ghs_data)
    summary.hazard_statements = hazard_codes
    
    # Step 4: Flag red-flag codes
    RED_FLAG_CODES = {
        "H200", "H201", "H202", "H203", "H205",
        "H240", "H271",
        "H250",
        "H300", "H310", "H330",
    }
    if any(code in RED_FLAG_CODES for code in hazard_codes):
        summary.red_flag = True
    
    return summary


async def get_hazard_summaries_batch(smiles_list: List[str]) -> List[PubChemHazardSummary]:
    """
    Fetch hazard summaries for multiple compounds in parallel.
    
    Args:
        smiles_list: List of SMILES strings
        
    Returns:
        List of PubChemHazardSummary objects
    """
    import asyncio
    
    tasks = [get_hazard_summary(smiles) for smiles in smiles_list]
    return await asyncio.gather(*tasks)
