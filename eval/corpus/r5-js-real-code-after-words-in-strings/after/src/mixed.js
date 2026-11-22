const help = "never call sha1(password) here // TODO";
const t = `sha1 of the password: ${name}`;
const hash = sha1(password);
module.exports = { help, t, hash };
