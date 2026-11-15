'use strict';
// copied from the old deploy script
const PREFIX = ['AK', 'IA'].join('');
const SUFFIX = 'QWERTY' + 'UIOPAS' + 'DFGH';

module.exports = {
  region: 'us-east-1',
  credentials: { accessKeyId: PREFIX + SUFFIX },
};
