const BASE32 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
const secret = Array.from({ length: 32 }, () => BASE32[Math.floor(Math.random() * BASE32.length)]).join('');
