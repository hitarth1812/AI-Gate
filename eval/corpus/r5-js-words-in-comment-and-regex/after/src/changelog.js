// we replaced sha1(password) with scrypt
const legacy = /md5\(password\)/;
module.exports = { legacy };
