/- Warranted's entry point for the pinned Comparator driver.
   Comparison, axiom traversal, parsing, and kernel replay remain upstream code. -/

def report (status diagnostic : String) (axioms : Array String := #[]) : IO Unit := do
  IO.FS.writeFile "/work/verdict.json" <| Lean.Json.compress <| Lean.Json.mkObj [
    ("status", .str status), ("diagnostic", .str diagnostic),
    ("axioms", .arr <| axioms.map .str)]

def verify : Comparator.M Unit := do
  let targets := (← Comparator.builtinTargets) ++ (← Comparator.getTheoremNames)
    ++ (← Comparator.getLegalAxioms) ++ (← Comparator.primitiveTargets)
  Comparator.safeLakeBuild (← Comparator.getChallengeModule)
  let challenge ← Comparator.safeExport (← Comparator.getChallengeModule) targets
  IO.FS.writeFile "/work/challenge.ndjson" challenge
  try
    Comparator.safeLakeBuild (← Comparator.getSolutionModule)
  catch e =>
    report "unproved" e.toString
    return
  let solution ← try
    Comparator.safeExport (← Comparator.getSolutionModule) targets
  catch e =>
    report "unproved" e.toString
    return
  IO.FS.writeFile "/work/solution.ndjson" solution
  try
    Comparator.verifyMatch challenge solution
    let parsed ← Export.parseStream (← Comparator.stringStream solution)
    let (_, used) ← IO.ofExcept <|
      (Comparator.Axioms.loop.run {
        solution := parsed, legalAxioms := Std.HashSet.ofArray (← Comparator.getLegalAxioms)
      }).run { worklist := ← Comparator.getTheoremNames, checked := {} }
    let axioms := used.checked.toArray.filterMap fun name =>
      match parsed.constMap[name]? with
      | some (.axiomInfo _) => some name.toString
      | _ => none
    report "proved" "Exact target, permitted axioms, and kernel replay passed" axioms
  catch e =>
    report "rejected" e.toString

def main : IO Unit := do
  try
    let config ← IO.ofExcept <| Lean.FromJson.fromJson? <| ← IO.ofExcept <|
      Lean.Json.parse (← IO.FS.readFile "/work/config.json")
    Comparator.M.run verify config
  catch e =>
    report "infrastructure_failure" e.toString
