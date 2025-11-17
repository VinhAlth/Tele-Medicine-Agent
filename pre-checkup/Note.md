# Curl thông tin bệnh nhân -> cá nhân hóa cho bot gọi tên và biết chuyên khoa khám

curl -s -X GET "https://api-gateway.dev.longvan.vn/clinic-service/callback/prescription/20.1387.1449" \
  -H "Content-Type: application/json" | jq '{
    patient: {
      id: .patient.id,
      name: .patient.name,
      phone: .patient.phone,
      birthDate: .patient.birthDate,
      gender: .patient.gender
    },
    doctor: {
      id: .doctor.id,
      name: .doctor.name,
      phone: .doctor.phone,
      specialization: .doctor.specialization
    }
  }'

