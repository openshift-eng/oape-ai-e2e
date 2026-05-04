package main

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"strings"
)

type FieldInfo struct {
	Name    string   `json:"name"`
	Type    string   `json:"type"`
	Tags    string   `json:"tags,omitempty"`
	Markers []string `json:"markers,omitempty"`
}

type StructInfo struct {
	Name    string      `json:"name"`
	Fields  []FieldInfo `json:"fields"`
	Markers []string    `json:"markers,omitempty"`
}

type FuncInfo struct {
	Name    string   `json:"name"`
	Params  []string `json:"params,omitempty"`
	Returns []string `json:"returns,omitempty"`
}

type ConstInfo struct {
	Name  string `json:"name"`
	Type  string `json:"type,omitempty"`
	Value string `json:"value,omitempty"`
}

type ExtractResult struct {
	Structs   []StructInfo `json:"structs"`
	Functions []FuncInfo   `json:"functions"`
	Markers   []string     `json:"markers"`
	Consts    []ConstInfo  `json:"consts"`
}

func extractFile(filename string) (*ExtractResult, error) {
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, filename, nil, parser.ParseComments)
	if err != nil {
		return nil, fmt.Errorf("parsing %s: %w", filename, err)
	}

	result := &ExtractResult{}

	src, err := os.ReadFile(filename)
	if err != nil {
		return nil, fmt.Errorf("reading %s: %w", filename, err)
	}
	lines := strings.Split(string(src), "\n")

	getMarkersBefore := func(pos token.Pos) []string {
		lineNum := fset.Position(pos).Line - 1
		var markers []string
		for i := lineNum - 1; i >= 0; i-- {
			trimmed := strings.TrimSpace(lines[i])
			if strings.HasPrefix(trimmed, "// +") {
				markers = append(markers, strings.TrimPrefix(trimmed, "// "))
			} else if trimmed == "" || strings.HasPrefix(trimmed, "//") {
				continue
			} else {
				break
			}
		}
		return markers
	}

	ast.Inspect(file, func(n ast.Node) bool {
		switch node := n.(type) {
		case *ast.GenDecl:
			if node.Tok == token.TYPE {
				for _, spec := range node.Specs {
					ts, ok := spec.(*ast.TypeSpec)
					if !ok {
						continue
					}
					st, ok := ts.Type.(*ast.StructType)
					if !ok {
						continue
					}

					info := StructInfo{
						Name:    ts.Name.Name,
						Markers: getMarkersBefore(node.Pos()),
					}

					if st.Fields != nil {
						for _, field := range st.Fields.List {
							if len(field.Names) == 0 {
								continue
							}
							fi := FieldInfo{
								Name:    field.Names[0].Name,
								Type:    exprToString(field.Type),
								Markers: getMarkersBefore(field.Pos()),
							}
							if field.Tag != nil {
								fi.Tags = field.Tag.Value
							}
							info.Fields = append(info.Fields, fi)
						}
					}

					result.Structs = append(result.Structs, info)
				}
			}

			if node.Tok == token.CONST {
				for _, spec := range node.Specs {
					vs, ok := spec.(*ast.ValueSpec)
					if !ok {
						continue
					}
					for i, name := range vs.Names {
						ci := ConstInfo{Name: name.Name}
						if vs.Type != nil {
							ci.Type = exprToString(vs.Type)
						}
						if i < len(vs.Values) {
							ci.Value = exprToString(vs.Values[i])
						}
						result.Consts = append(result.Consts, ci)
					}
				}
			}

		case *ast.FuncDecl:
			fi := FuncInfo{Name: node.Name.Name}
			if node.Type.Params != nil {
				for _, p := range node.Type.Params.List {
					ptype := exprToString(p.Type)
					for _, name := range p.Names {
						fi.Params = append(fi.Params, name.Name+" "+ptype)
					}
					if len(p.Names) == 0 {
						fi.Params = append(fi.Params, ptype)
					}
				}
			}
			if node.Type.Results != nil {
				for _, r := range node.Type.Results.List {
					fi.Returns = append(fi.Returns, exprToString(r.Type))
				}
			}
			result.Functions = append(result.Functions, fi)
		}
		return true
	})

	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "// +kubebuilder:") || strings.HasPrefix(trimmed, "// +optional") {
			result.Markers = append(result.Markers, strings.TrimPrefix(trimmed, "// "))
		}
	}

	return result, nil
}

func exprToString(expr ast.Expr) string {
	switch e := expr.(type) {
	case *ast.Ident:
		return e.Name
	case *ast.StarExpr:
		return "*" + exprToString(e.X)
	case *ast.SelectorExpr:
		return exprToString(e.X) + "." + e.Sel.Name
	case *ast.ArrayType:
		return "[]" + exprToString(e.Elt)
	case *ast.MapType:
		return "map[" + exprToString(e.Key) + "]" + exprToString(e.Value)
	case *ast.BasicLit:
		return e.Value
	default:
		return fmt.Sprintf("%T", expr)
	}
}

func main() {
	if len(os.Args) < 3 {
		fmt.Fprintf(os.Stderr, "Usage: %s extract --file <path>\n", os.Args[0])
		os.Exit(1)
	}

	cmd := os.Args[1]
	if cmd != "extract" {
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n", cmd)
		os.Exit(1)
	}

	var filename string
	for i, arg := range os.Args[2:] {
		if arg == "--file" && i+3 < len(os.Args) {
			filename = os.Args[i+3]
		}
	}
	if filename == "" {
		fmt.Fprintln(os.Stderr, "Missing --file argument")
		os.Exit(1)
	}

	result, err := extractFile(filename)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	enc.Encode(result)
}
