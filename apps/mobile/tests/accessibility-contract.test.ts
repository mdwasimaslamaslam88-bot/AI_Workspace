/// <reference types="node" />

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { describe, expect, it } from "vitest";

function sourceFiles(root: string): string[] {
  return fs.readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(root, entry.name);
    if (entry.isDirectory()) return sourceFiles(target);
    return entry.isFile() && target.endsWith(".tsx") ? [target] : [];
  });
}

describe("mobile accessibility contract", () => {
  it("gives every native Pressable an explicit accessible role", () => {
    const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src");
    const missing: string[] = [];
    for (const filename of sourceFiles(root)) {
      const source = ts.createSourceFile(
        filename,
        fs.readFileSync(filename, "utf8"),
        ts.ScriptTarget.Latest,
        true,
        ts.ScriptKind.TSX,
      );
      const visit = (node: ts.Node) => {
        if (
          (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) &&
          node.tagName.getText(source) === "Pressable"
        ) {
          const hasRole = node.attributes.properties.some(
            (attribute) =>
              ts.isJsxAttribute(attribute) &&
              attribute.name.getText(source) === "accessibilityRole",
          );
          if (!hasRole) {
            const position = source.getLineAndCharacterOfPosition(node.getStart(source));
            missing.push(`${path.relative(root, filename)}:${position.line + 1}`);
          }
        }
        ts.forEachChild(node, visit);
      };
      visit(source);
    }
    expect(missing).toEqual([]);
  });

  it("shows every required validated-update decision field", () => {
    const settings = fs.readFileSync(
      path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/app/settings.tsx"),
      "utf8",
    );
    for (const label of [
      "WORK STATION UPDATE READY",
      "Version:",
      "What changed:",
      "Benefits:",
      "Performance:",
      "Quality:",
      "Security:",
      "Compatibility:",
      "Rollback checkpoint:",
      ">UPDATE<",
      ">CANCEL<",
    ]) {
      expect(settings).toContain(label);
    }
  });
});
