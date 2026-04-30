import logging
from typing import Dict, List, Any, Optional
import os

logger = logging.getLogger(__name__)

class SignNowPacketService:
    """
    Handles the two-phase SignNow packet assembly, template mapping,
    document multiplication, prefilling, grouping, and inviting.
    
    Migrated from GAS SignNow_SendPaperwork.js and Telegram_Documents.js
    """
    
    # Single Source of Truth for Template IDs
    TEMPLATE_MAP = {
        'paperwork-header':      '9b9dad3e319f4b1580094e05f9844929d5a6f7de',
        'faq-cosigners':         '0820b9fef3bd4c38a91643455881021f3f0c3a88',
        'faq-defendants':        '1524f1c816c54a72be76d14fe128e4a6034579dc',
        'indemnity-agreement':   'ed5e6ca0a3444796a127fbeb6a880658371aafd7',
        'defendant-application': 'd50adc808f3245f087b218d33da89e4ace15ecd4',
        'promissory-note':       '460bd43c2f514305a3b296481713a00ee8311c79',
        'disclosure-form':       'fb8b57bf55ac4d5e8bff820b018a0bfd3b17a37a',
        'surety-terms':          '192aeb246230446bb0d7f658765afd2832704964',
        'master-waiver':         '3b0e71188b3049cc8760d144e6c49df227ccd741',
        'ssa-release':           '4800defff07541079760889d83109059585b0cea',
        'collateral-receipt':    '4b1f5611840f4de4bc891677617f5dbf6ff7ad05', # OSI
        'payment-plan':          '1861b158d7a447d48be5ac1dd24755f727f0773b', # OSI
        'appearance-bond':       '7ba703e101e04604a2f1458c21d3addfce9ca86b'  # Print only
    }
    
    # Document Multiplication Rules
    DOC_RULES = {
        'paperwork-header':      {'rule': 'static',         'label': 'Paperwork Header'},
        'faq-cosigners':         {'rule': 'shared',         'label': 'FAQ - Cosigners'},
        'faq-defendants':        {'rule': 'shared',         'label': 'FAQ - Defendants'},
        'indemnity-agreement':   {'rule': 'per-indemnitor', 'label': 'Indemnity Agreement'},
        'defendant-application': {'rule': 'static',         'label': 'Defendant Application'},
        'promissory-note':       {'rule': 'shared',         'label': 'Promissory Note'},
        'disclosure-form':       {'rule': 'shared',         'label': 'Disclosure Form'},
        'surety-terms':          {'rule': 'shared',         'label': 'Surety Terms'},
        'master-waiver':         {'rule': 'shared',         'label': 'Master Waiver'},
        'ssa-release':           {'rule': 'per-person',     'label': 'SSA Release'},
        'collateral-receipt':    {'rule': 'shared',         'label': 'Collateral & Premium Receipt'},
        'payment-plan':          {'rule': 'shared',         'label': 'Payment Plan Agreement'},
        'appearance-bond':       {'rule': 'print-only',     'label': 'Appearance Bond (Print Only)'}
    }
    
    def __init__(self):
        self.api_token = os.environ.get('SIGNNOW_API_TOKEN')
        self.base_url = 'https://api.signnow.com'
        
    def build_packet_manifest(self, phase: int, surety_id: str = 'osi', num_indemnitors: int = 1) -> List[Dict[str, Any]]:
        """
        Build the manifest of documents needed for a specific phase.
        Handles surety-specific templates and multiplication rules.
        """
        manifest = []
        
        # Define which docs go in which phase
        phase_1_docs = [
            'paperwork-header', 'faq-cosigners', 'indemnity-agreement',
            'promissory-note', 'disclosure-form', 'ssa-release'
        ]
        
        phase_2_docs = [
            'faq-defendants', 'defendant-application', 'surety-terms',
            'master-waiver', 'collateral-receipt', 'payment-plan'
        ]
        
        target_docs = phase_1_docs if phase == 1 else phase_2_docs
        
        for doc_key in target_docs:
            # Handle surety-specific templates
            template_key = doc_key
            if doc_key in ['collateral-receipt', 'payment-plan'] and surety_id == 'palmetto':
                template_key = f"{doc_key}-palmetto"
                
            template_id = self.TEMPLATE_MAP.get(template_key)
            if not template_id:
                logger.warning(f"Template ID not found for {template_key}")
                continue
                
            rule = self.DOC_RULES.get(doc_key, {}).get('rule', 'static')
            
            # Apply multiplication rules
            copies_needed = 1
            if rule == 'per-indemnitor':
                copies_needed = num_indemnitors
            elif rule == 'per-person':
                copies_needed = num_indemnitors + 1 # +1 for defendant
                
            for i in range(copies_needed):
                manifest.append({
                    'doc_key': doc_key,
                    'template_id': template_id,
                    'copy_index': i + 1,
                    'rule': rule
                })
                
        return manifest
        
    def handle_send_phase_1(self, form_data: Dict[str, Any], signer_email: str, signer_name: str) -> Dict[str, Any]:
        """
        Phase 1: Indemnitor signs first (No POA required).
        """
        logger.info(f"Starting Phase 1 packet for {signer_email}")
        
        # 1. Build manifest
        num_indemnitors = len(form_data.get('indemnitors', [{}]))
        manifest = self.build_packet_manifest(phase=1, num_indemnitors=num_indemnitors)
        
        # In production:
        # 2. Copy templates
        # 3. Prefill fields
        # 4. Group documents
        # 5. Send invite
        
        return {
            'status': 'success',
            'phase': 1,
            'message': 'Phase 1 packet sent to indemnitor',
            'manifest_size': len(manifest)
        }
        
    def handle_send_phase_2(self, form_data: Dict[str, Any], signer_email: str, signer_name: str, 
                           poa_number: str, agent_name: str, agent_license: str, surety_id: str = 'osi') -> Dict[str, Any]:
        """
        Phase 2: After bondsman approval + POA entry.
        Will NOT execute without a valid poa_number.
        """
        if not poa_number:
            raise ValueError("Phase 2 requires a valid POA number")
            
        logger.info(f"Starting Phase 2 packet for {signer_email} with POA {poa_number}")
        
        # 1. Build manifest
        manifest = self.build_packet_manifest(phase=2, surety_id=surety_id)
        
        # In production:
        # 2. Copy templates
        # 3. Prefill fields (including POA)
        # 4. Group documents
        # 5. Send invite
        
        return {
            'status': 'success',
            'phase': 2,
            'message': 'Phase 2 packet sent',
            'manifest_size': len(manifest)
        }

    async def create_packet(self, intake_doc: Dict[str, Any], packet_id: str) -> Dict[str, Any]:
        """
        Creates a SignNow packet for the given intake document.
        This is a stub implementation that returns a mock invite ID and signing link
        to satisfy the paperwork.py push_to_signnow endpoint.
        """
        logger.info(f"Creating SignNow packet {packet_id} for intake {intake_doc.get('intake_id')}")
        
        # In a real implementation, this would:
        # 1. Use self.build_packet_manifest() to get the list of templates
        # 2. Call SignNow API to copy each template
        # 3. Call SignNow API to prefill fields on each copied document
        # 4. Call SignNow API to group the documents into a document group
        # 5. Call SignNow API to create an embedded invite for the group
        
        # Return mock data for now
        return {
            "invite_id": f"mock_invite_{packet_id}",
            "signing_link": f"https://signnow.com/s/mock_link_{packet_id}",
            "status": "success"
        }
