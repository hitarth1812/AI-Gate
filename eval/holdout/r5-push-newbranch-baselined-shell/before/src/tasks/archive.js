const { exec } = require("child_process");

function unpack(name, done) {
  exec("tar -xf " + name, done);
}

module.exports = { unpack };
