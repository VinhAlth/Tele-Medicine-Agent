lk dispatch create \
    --new-room \
    --agent-name outbound-caller \
    --metadata '{"phone_number": "3900102"}'


lk dispatch create \
  --new-room \
  --agent-name outbound-caller \
  --metadata '{"phone_number": "3800103", "transfer_to": "3900102"}'