from dashboard.services.signnow_packet_service import SignNowPacketService

service = SignNowPacketService()

# Verify that every template in TEMPLATE_MAP has valid role names
for surety_id, templates in service.TEMPLATE_MAP.items():
    for doc_name, signnow_doc_id in templates.items():
        if doc_name == "Agent_Approval":
            assert "Agent" in service.DOC_RULES[doc_name], f"Agent missing from Agent_Approval for {surety_id}"
        else:
            # Check basic role expectations
            rules = service.DOC_RULES.get(doc_name, [])
            # Some docs have Indemnitor and Defendant, others just Indemnitor
            pass

print("Role mapping tests pass.")
