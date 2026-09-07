'use strict';
// query-string 7 consumes a CommonJS function; upstream 0.5 exposes a default.
const decodeUriComponent = require('decode-uri-component-upstream').default;
module.exports = function decodeUriComponentCompat(encodedURI) {
  // Preserve the old consumer's form-encoded plus handling, including %2B.
  return decodeUriComponent(
    typeof encodedURI === 'string' ? encodedURI.replace(/\+/g, ' ') : encodedURI
  );
};
