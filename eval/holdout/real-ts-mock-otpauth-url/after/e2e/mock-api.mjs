const secret = 'JBSWY3DPEHPK3PXP';
const otpauthUrl = `otpauth://totp/ATLAS:${encodeURIComponent(email)}?secret=${secret}&issuer=ATLAS`;
