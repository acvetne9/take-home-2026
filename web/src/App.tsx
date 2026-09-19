import { Route, Routes } from "react-router-dom";
import Catalog from "@/pages/Catalog";
import ProductDetail from "@/pages/ProductDetail";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Catalog />} />
      <Route path="/product/:id" element={<ProductDetail />} />
    </Routes>
  );
}
